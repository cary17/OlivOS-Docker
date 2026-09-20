import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import download_plugins


class OpkValidationTests(unittest.TestCase):
    def test_manifest_parser_rejects_invalid_sources(self):
        for line in ('bad.opk:https://example.com/owner/repo', 'not a manifest entry'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                download_plugins.parse_manifest_line(line)

    def test_asset_selection_prefers_opk_and_falls_back_to_zip(self):
        zip_asset = {'name': 'demo.zip', 'id': 1}
        opk_asset = {'name': 'demo.opk', 'id': 2}
        self.assertEqual(download_plugins.select_release_asset({'assets': [zip_asset]}, 'demo.opk'), zip_asset)
        self.assertEqual(download_plugins.select_release_asset({'assets': [zip_asset, opk_asset]}, 'demo.opk'), opk_asset)
        self.assertIsNone(download_plugins.select_release_asset({'assets': [opk_asset]}, 'missing.opk'))
        self.assertIsNone(download_plugins.select_release_asset(
            {'assets': [zip_asset, {'name': 'other.zip'}]}, 'missing.opk'))

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

    def test_normalize_release_zip_strips_single_plugin_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Demo/app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('Demo/__init__.py', '')
                archive.writestr('Demo/main.py', '')

            metadata = download_plugins.normalize_release_zip(path)

            self.assertEqual(metadata['namespace'], 'demo')
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {'app.json', '__init__.py', 'main.py'})

    def test_sha256_file_returns_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'file.opk'
            path.write_bytes(b'olivos')

            self.assertEqual(
                download_plugins.sha256_file(path),
                hashlib.sha256(b'olivos').hexdigest(),
            )


class DownloadTests(unittest.TestCase):
    def test_two_webui_assets_are_downloaded_from_one_release(self):
        names = ['OlivaDiceWebUI.opk', 'OlivaDiceWebUIStandalone.opk']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text(''.join(
                f'{name}:https://github.com/ShiaNyaa/OlivaDiceWebUI/releases\n' for name in names))
            release = {'tag_name': 'v20260920(16)', 'assets': [
                {'name': name, 'label': f'UI timestamp version {index}',
                 'browser_download_url': f'https://example.com/{name}'}
                for index, name in enumerate(reversed(names))
            ]}

            def fake_download(url, destination, validator):
                self.assertTrue(url.endswith('/' + destination.name))
                with zipfile.ZipFile(destination, 'w') as archive:
                    archive.writestr('app.json', json.dumps({'namespace': destination.stem}))
                    archive.writestr('__init__.py', '')
                    archive.writestr('main.py', '')
                validator(destination)

            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value=release) as request,
                mock.patch.object(download_plugins, 'download_file', side_effect=fake_download),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            request.assert_called_once()
            self.assertEqual([item['asset'] for item in result], names)
            self.assertEqual(len({item['namespace'] for item in result}), 2)
            self.assertTrue(all((root / 'plugins' / name).is_file() for name in names))

    def test_local_override_manifest_matches_installed_bytes_and_copy_errors_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / 'local'
            local.mkdir()
            source = local / 'demo.opk'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo', 'version': 'local'}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            output = root / 'installed'
            manifest = root / 'manifest.json'
            release = {'tag_name': 'remote', 'assets': [
                {'name': 'demo.opk', 'browser_download_url': 'https://example.com/demo.opk'}
            ]}

            def fake_download(url, destination, validator):
                destination.write_bytes(source.read_bytes())
                validator(destination)

            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', manifest),
                mock.patch.object(download_plugins, 'request_json', return_value=release),
                mock.patch.object(download_plugins, 'download_file', side_effect=fake_download),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=local)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['source'], 'local')
                self.assertEqual(result[0]['version'], 'local')
                self.assertEqual(result[0]['sha256'], download_plugins.sha256_file(output / 'demo.opk'))
                self.assertEqual(json.loads(manifest.read_text()), result)
                manifest.unlink()
                with mock.patch.object(download_plugins.shutil, 'copyfile', side_effect=OSError('copy failed')):
                    with self.assertRaises(OSError):
                        download_plugins.download_plugins(opk_list, local_dir=local)
                self.assertFalse(manifest.exists())

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