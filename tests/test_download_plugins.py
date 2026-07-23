import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import download_plugins


class OpkValidationTests(unittest.TestCase):
    def test_validate_opk_requires_native_entry_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'broken.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('app.json', '{}')

            with self.assertRaises(ValueError):
                download_plugins.validate_opk(path)

    def test_validate_opk_accepts_native_plugin_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'valid.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')

            metadata = download_plugins.validate_opk(path)

            self.assertEqual(metadata['namespace'], 'demo')

    def test_sha256_file_returns_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file.opk'
            path.write_bytes(b'olivos')

            self.assertEqual(
                download_plugins.sha256_file(path),
                hashlib.sha256(b'olivos').hexdigest(),
            )


class DownloadTests(unittest.TestCase):
    def test_download_file_retries_and_atomically_replaces_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'plugin.opk'
            attempts = 0

            def fake_urlopen(request, timeout):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError('temporary failure')
                response = mock.MagicMock()
                response.__enter__.return_value.read.side_effect = [b'new-data', b'']
                return response

            with (
                mock.patch('download_plugins.urllib.request.urlopen', side_effect=fake_urlopen),
                mock.patch('download_plugins.time.sleep'),
            ):
                download_plugins.download_file('https://example.com/plugin.opk', destination, retries=2)

            self.assertEqual(destination.read_bytes(), b'new-data')
            self.assertEqual(attempts, 2)
            self.assertFalse(destination.with_suffix('.opk.tmp').exists())

    def test_download_file_validates_before_replacing_known_good_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'plugin.opk'
            destination.write_bytes(b'known-good')
            response = mock.MagicMock()
            response.__enter__.return_value.read.side_effect = [b'bad-data', b'']

            with (
                mock.patch('download_plugins.urllib.request.urlopen', return_value=response),
                self.assertRaises(ValueError),
            ):
                download_plugins.download_file(
                    'https://example.com/plugin.opk',
                    destination,
                    validator=lambda path: (_ for _ in ()).throw(ValueError('invalid')),
                )

            self.assertEqual(destination.read_bytes(), b'known-good')
            self.assertFalse(destination.with_suffix('.opk.tmp').exists())


if __name__ == '__main__':
    unittest.main()