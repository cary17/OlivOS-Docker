import hashlib
import io
import json
import multiprocessing
import os
import queue
import stat
import tempfile
import time
import unittest
import urllib.error
import warnings
import zipfile
from pathlib import Path
from unittest import mock

import download_plugins


def small_budgets(**overrides):
    """Fixed small budgets so limit tests stay tiny and fast."""
    budgets = {
        'download': 1024 * 1024,
        'member': 1024 * 1024,
        'archive': 2 * 1024 * 1024,
        'members': 100,
        'app_json': 64 * 1024,
        'repack': 1024 * 1024,
        'total': 4 * 1024 * 1024,
        'time': 20,
        'address_space': 512 * 1024**2,
        'ratio': 100,
    }
    budgets.update(overrides)
    return budgets


def write_opk(path, namespace='demo', version=None, prefix=''):
    with zipfile.ZipFile(path, 'w') as archive:
        app = {'namespace': namespace}
        if version is not None:
            app['version'] = version
        archive.writestr(prefix + 'app.json', json.dumps(app))
        archive.writestr(prefix + '__init__.py', '')
        archive.writestr(prefix + 'main.py', '')
    return path


def fake_worker(namespace='demo', version='1.0', fail=False):
    """In-process stand-in for the spawned asset worker (fake downloader seam)."""
    def runner(job):
        if fail:
            raise download_plugins.AssetError('fake worker failure')
        payload = Path(job['staging_dir']) / 'payload.bin'
        source = job.get('url') or job['source']
        write_opk(payload, namespace=namespace or Path(source).stem, version=version)
        return {'ok': True, 'namespace': namespace or Path(source).stem, 'version': version}
    return runner


class OpkValidationTests(unittest.TestCase):
    def test_manifest_parser_rejects_invalid_sources(self):
        for line in ('bad.opk:https://example.com/owner/repo', 'not a manifest entry'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                download_plugins.parse_manifest_line(line)

    def test_manifest_parser_rejects_paths_and_malformed_repositories(self):
        for name in ('../demo.opk', '/tmp/demo.opk', r'..\demo.opk', r'C:\demo.opk',
                     '.opk', 'demo\x01.opk'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                download_plugins.parse_manifest_line(f'{name}\uff1ahttps://github.com/owner/repo')
        for repo in ('/repo', 'owner//releases', 'owner/', 'owner/repo//',
                     'owner/repo?x=1', 'owner/repo#fragment', 'owner/..',
                     'owner/repo/other', r'owner/repo\other'):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                download_plugins.parse_manifest_line(f'demo.opk:https://github.com/{repo}')

    def test_manifest_parser_preserves_supported_repository_urls(self):
        for suffix in ('', '/', '/releases', '/releases/'):
            for separator in (':', '\uff1a'):
                with self.subTest(suffix=suffix, separator=separator):
                    self.assertEqual(download_plugins.parse_manifest_line(
                        f' demo.opk{separator}https://github.com/owner-name/repo_name.v1{suffix} '),
                        ('demo.opk', 'owner-name/repo_name.v1'))

    def test_manifest_parser_rejects_bom_inside_names_with_visible_form(self):
        for line in ('\ufeffdemo.opk:https://github.com/owner/repo',
                     'demo\ufeff.opk：https://github.com/owner/repo'):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as caught:
                    download_plugins.parse_manifest_line(line)
                self.assertIn('U+FEFF', str(caught.exception))
                self.assertIn('\\ufeff', str(caught.exception))  # 可见的转义表示

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

    def test_standard_opk_keeps_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_opk(Path(tmp) / 'demo.opk', namespace='demo')
            before = path.read_bytes()
            metadata = download_plugins.validate_opk(path, small_budgets(), raw_size=path.stat().st_size)
            self.assertEqual(metadata['namespace'], 'demo')
            self.assertEqual(path.read_bytes(), before)

    def test_normalize_release_zip_strips_single_plugin_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Demo/app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('Demo/__init__.py', '')
                archive.writestr('Demo/main.py', '')

            metadata = download_plugins.normalize_release_zip(path, small_budgets())

            self.assertEqual(metadata['namespace'], 'demo')
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {'app.json', '__init__.py', 'main.py'})

    def test_normalize_release_zip_accepts_root_entries_and_nested_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')
                archive.writestr('assets/', '')
                archive.writestr('assets/icon.txt', 'resource')
                archive.writestr('data/config.json', '{}')
            self.assertEqual(download_plugins.normalize_release_zip(path, small_budgets())['namespace'], 'demo')
            with zipfile.ZipFile(path) as archive:
                self.assertIn('assets/icon.txt', archive.namelist())
                self.assertIn('data/config.json', archive.namelist())

    def test_normalize_release_zip_rejects_mixed_or_incomplete_layouts(self):
        layouts = (
            ('Demo/app.json', 'Demo/__init__.py', 'Other/main.py'),
            ('Demo/app.json', 'Demo/__init__.py', 'Demo/main.py', 'README.md'),
            ('app.json', '__init__.py'),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            for names in layouts:
                with self.subTest(names=names):
                    with zipfile.ZipFile(path, 'w') as archive:
                        for name in names:
                            archive.writestr(name, json.dumps({'namespace': 'demo'})
                                             if name.endswith('app.json') else '')
                    before = path.read_bytes()
                    with self.assertRaises(ValueError):
                        download_plugins.normalize_release_zip(path, small_budgets())
                    self.assertEqual(path.read_bytes(), before)
                    self.assertFalse(path.with_suffix('.opk.normalized').exists())

    def test_archives_reject_unsafe_member_paths_before_normalizing(self):
        members = ('../escape.txt', '/absolute.txt', r'..\escape.txt', r'C:\escape.txt',
                   'C:/escape.txt', 'nested/../escape.txt', 'nested//escape.txt',
                   './escape.txt', 'bad\x01.txt')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            for validator, prefix in ((download_plugins.validate_opk, ''),
                                      (download_plugins.normalize_release_zip, ''),
                                      (download_plugins.normalize_release_zip, 'Demo/')):
                for member in members:
                    with self.subTest(validator=validator.__name__, prefix=prefix, member=member):
                        with zipfile.ZipFile(path, 'w') as archive:
                            archive.writestr(prefix + 'app.json', json.dumps({'namespace': 'demo'}))
                            archive.writestr(prefix + '__init__.py', '')
                            archive.writestr(prefix + 'main.py', '')
                            archive.writestr(prefix + member, 'fixture')
                        before = path.read_bytes()
                        with self.assertRaises(ValueError):
                            validator(path, small_budgets())
                        self.assertEqual(path.read_bytes(), before)
                        self.assertFalse(path.with_suffix('.opk.normalized').exists())

    def test_archives_reject_duplicate_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            for validator, prefix in ((download_plugins.validate_opk, ''),
                                      (download_plugins.normalize_release_zip, 'Demo/')):
                with self.subTest(validator=validator.__name__, prefix=prefix):
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        with zipfile.ZipFile(path, 'w') as archive:
                            archive.writestr(prefix + 'app.json', json.dumps({'namespace': 'first'}))
                            archive.writestr(prefix + '__init__.py', '')
                            archive.writestr(prefix + 'main.py', '')
                            archive.writestr(prefix + 'app.json', json.dumps({'namespace': 'last'}))
                    before = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'Duplicate ZIP member'):
                        validator(path, small_budgets())
                    self.assertEqual(path.read_bytes(), before)

    def test_validate_opk_requires_nonempty_string_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plugin.opk'
            for namespace in (None, '', '   ', 123, True, [], ['demo'], {'name': 'demo'}):
                with self.subTest(namespace=namespace):
                    with zipfile.ZipFile(path, 'w') as archive:
                        archive.writestr('app.json', json.dumps({'namespace': namespace}))
                        archive.writestr('__init__.py', '')
                        archive.writestr('main.py', '')
                    with self.assertRaises(ValueError):
                        download_plugins.validate_opk(path, small_budgets())

    def test_app_json_budget_is_enforced_during_the_same_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plugin.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo', 'pad': 'x' * 4096}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')
            with self.assertRaisesRegex(download_plugins.AssetError, 'app.json exceeds'):
                download_plugins.validate_opk(path, small_budgets(app_json=1024))

    def test_member_and_archive_budgets_reject_oversized_declared_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plugin.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')
                archive.writestr('data.bin', b'\0' * 4096)
            with self.subTest(limit='member'):
                with self.assertRaisesRegex(download_plugins.AssetError, 'member declares'):
                    download_plugins.validate_opk(path, small_budgets(member=1024))
            with self.subTest(limit='archive'):
                with self.assertRaisesRegex(download_plugins.AssetError, 'declares more than'):
                    download_plugins.validate_opk(path, small_budgets(archive=2048))

    def test_member_count_budget_rejects_many_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plugin.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')
                for index in range(5):
                    archive.writestr(f'assets/file{index}.txt', 'x')
            with self.assertRaisesRegex(download_plugins.AssetError, 'more than 5 members'):
                download_plugins.validate_opk(path, small_budgets(members=5))

    def test_repack_output_budget_rejects_large_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.opk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Demo/app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('Demo/__init__.py', '')
                archive.writestr('Demo/main.py', '')
                archive.writestr('Demo/data.bin', b'\0' * 4096)
            with self.assertRaisesRegex(download_plugins.AssetError, 'repacked archive exceeds'):
                download_plugins.normalize_release_zip(path, small_budgets(repack=1024))

    def test_expansion_ratio_prefilter_uses_actual_file_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plugin.opk'
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('app.json', json.dumps({'namespace': 'demo'}))
                archive.writestr('__init__.py', '')
                archive.writestr('main.py', '')
                archive.writestr('data.bin', b'\0' * (512 * 1024))
            self.assertLess(path.stat().st_size, 64 * 1024)
            with self.assertRaisesRegex(download_plugins.AssetError, 'ratio limit'):
                download_plugins.validate_opk(
                    path, small_budgets(ratio=100), raw_size=path.stat().st_size)

    def test_declared_size_mismatch_is_rejected(self):
        info = zipfile.ZipInfo('app.json')
        info.file_size = 10
        info.compress_size = 10
        info.CRC = 0

        class FakeArchive:
            def __init__(self, payload):
                self.payload = payload

            def open(self, target):
                return io.BytesIO(self.payload)

        with self.assertRaisesRegex(download_plugins.AssetError, 'declares 10 bytes but expanded to 20'):
            download_plugins._read_member_bounded(
                FakeArchive(b'x' * 20), info, small_budgets())

    def test_corrupt_crc_is_rejected_without_repack(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plugin.opk'
            write_opk(path)
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo('app.json')
                offset = info.header_offset
            with path.open('rb') as raw_file:
                raw_file.seek(offset + 26)
                name_len = int.from_bytes(raw_file.read(2), 'little')
                extra_len = int.from_bytes(raw_file.read(2), 'little')
            data_offset = offset + 30 + name_len + extra_len
            raw = bytearray(path.read_bytes())
            raw[data_offset] ^= 0xFF
            path.write_bytes(bytes(raw))
            with self.assertRaises(ValueError):
                download_plugins.validate_opk(path, small_budgets())

    def test_encrypted_symlink_and_special_members_are_rejected(self):
        infos = []
        encrypted = zipfile.ZipInfo('app.json')
        encrypted.flag_bits |= 0x1
        infos.append(('encrypted', encrypted))
        symlink = zipfile.ZipInfo('link.json')
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        infos.append(('symlink', symlink))
        fifo = zipfile.ZipInfo('pipe.json')
        fifo.external_attr = (stat.S_IFIFO | 0o644) << 16
        infos.append(('special', fifo))
        big_dir = zipfile.ZipInfo('assets/')
        big_dir.file_size = 8
        infos.append(('dir-content', big_dir))

        class FakeArchive:
            def infolist(self):
                return [info for _, info in infos]

            def open(self, info):
                return io.BytesIO(b'')

        for label, info in infos:
            with self.subTest(member=label):
                with self.assertRaises((download_plugins.AssetError, ValueError)):
                    download_plugins._validate_archive_members(FakeArchive(), small_budgets())

    def test_unrecorded_unix_mode_stays_compatible(self):
        info = zipfile.ZipInfo('app.json')
        info.file_size = 2
        info.compress_size = 2

        class FakeArchive:
            def infolist(self):
                return [info]

            def open(self, target):
                return io.BytesIO(b'{}')

        members, app_bytes = download_plugins._validate_archive_members(FakeArchive(), small_budgets())
        self.assertEqual([member.filename for member in members], ['app.json'])
        self.assertEqual(app_bytes, b'{}')


class FakeResponse:
    """Reusable HTTP response stand-in that survives multiple retry attempts."""

    def __init__(self, chunks, headers=None):
        self._chunks = list(chunks)
        self.headers = headers or {}

    def read1(self, size):
        if self._chunks:
            return self._chunks.pop(0)
        return b''

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class DownloadByteBudgetTests(unittest.TestCase):
    def make_response(self, chunks, headers=None):
        return FakeResponse(chunks, headers)

    def test_content_length_over_limit_fails_before_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            response = self.make_response([b'data'], headers={'Content-Length': str(10 * 1024)})
            with mock.patch('download_plugins.urllib.request.urlopen', return_value=response):
                with self.assertRaisesRegex(download_plugins.AssetError, 'declares 10240 bytes'):
                    download_plugins._download_bounded(
                        'https://example.com/asset.bin', destination, small_budgets(download=1024),
                        deadline=time.monotonic() + 5)
            self.assertFalse(destination.exists())

    def test_missing_length_still_counts_actual_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            response = self.make_response([b'a' * 600, b'b' * 600])
            with mock.patch('download_plugins.urllib.request.urlopen', return_value=response):
                total = download_plugins._download_bounded(
                    'https://example.com/asset.bin', destination, small_budgets(download=2048),
                    deadline=time.monotonic() + 5)
            self.assertEqual(total, 1200)
            self.assertEqual(destination.read_bytes(), b'a' * 600 + b'b' * 600)

    def test_cross_chunk_over_limit_is_permanent(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            response = self.make_response([b'a' * 600, b'b' * 600])
            with mock.patch('download_plugins.urllib.request.urlopen', return_value=response):
                with self.assertRaisesRegex(download_plugins.AssetError, 'exceeded the 1024 byte limit'):
                    download_plugins._download_bounded(
                        'https://example.com/asset.bin', destination, small_budgets(download=1024),
                        deadline=time.monotonic() + 5)
            self.assertFalse(destination.exists())

    def test_truncation_is_retried_then_committed_when_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            short = self.make_response([b'12345'], headers={'Content-Length': '8'})
            full = self.make_response([b'12345678'], headers={'Content-Length': '8'})
            with (
                mock.patch('download_plugins.urllib.request.urlopen', side_effect=[short, full]),
                mock.patch('download_plugins.time.sleep'),
            ):
                total = download_plugins._download_bounded(
                    'https://example.com/asset.bin', destination, small_budgets(),
                    deadline=time.monotonic() + 5)
            self.assertEqual(total, 8)
            self.assertEqual(destination.read_bytes(), b'12345678')

    def test_persistent_truncation_fails_without_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            known_good = destination.parent / 'plugin.opk'
            known_good.write_bytes(b'known-good')
            response = self.make_response([b'12345'], headers={'Content-Length': '8'})
            with (
                mock.patch('download_plugins.urllib.request.urlopen', return_value=response),
                mock.patch('download_plugins.time.sleep'),
            ):
                with self.assertRaisesRegex(download_plugins.AssetError, 'failed to download'):
                    download_plugins._download_bounded(
                        'https://example.com/asset.bin', destination, small_budgets(),
                        deadline=time.monotonic() + 5, retries=2)
            self.assertFalse(destination.exists())
            self.assertEqual(known_good.read_bytes(), b'known-good')

    def test_client_errors_fail_fast_without_retry(self):
        for code in (401, 403, 404):
            with self.subTest(code=code):
                with tempfile.TemporaryDirectory() as tmp:
                    destination = Path(tmp) / 'asset.bin'
                    error = urllib.error.HTTPError('https://example.com/a', code, 'nope', {}, None)
                    with (
                        mock.patch('download_plugins.urllib.request.urlopen', side_effect=error),
                        mock.patch('download_plugins.time.sleep') as sleeper,
                    ):
                        with self.assertRaisesRegex(download_plugins.AssetError, f'HTTP {code}'):
                            download_plugins._download_bounded(
                                'https://example.com/a', destination, small_budgets(),
                                deadline=time.monotonic() + 5)
                    sleeper.assert_not_called()

    def test_server_errors_are_retried_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            error = urllib.error.HTTPError('https://example.com/a', 503, 'busy', {}, None)
            response = self.make_response([b'ok'])
            with (
                mock.patch('download_plugins.urllib.request.urlopen', side_effect=[error, response]),
                mock.patch('download_plugins.time.sleep'),
            ):
                total = download_plugins._download_bounded(
                    'https://example.com/a', destination, small_budgets(),
                    deadline=time.monotonic() + 5)
            self.assertEqual(total, 2)

    def test_download_budget_is_not_reset_by_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'asset.bin'
            clock = {'now': 0.0}
            with (
                mock.patch.object(download_plugins.time, 'monotonic', lambda: clock['now']),
                mock.patch.object(download_plugins.time, 'sleep',
                                  lambda seconds: clock.__setitem__('now', clock['now'] + seconds)),
                mock.patch('download_plugins.urllib.request.urlopen', side_effect=OSError('down')),
                self.assertRaisesRegex(download_plugins.AssetError, 'budget exhausted before attempt 2'),
            ):
                download_plugins._download_bounded(
                    'https://example.com/asset.bin', destination, small_budgets(), deadline=0.5)

    def test_download_file_returns_validator_metadata_and_keeps_good_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'plugin.opk'
            destination.write_bytes(b'known-good')
            response = self.make_response([b'bad'])
            with (
                mock.patch('download_plugins.urllib.request.urlopen', return_value=response),
                self.assertRaises(ValueError),
            ):
                download_plugins.download_file(
                    'https://example.com/plugin.opk',
                    destination,
                    budgets=small_budgets(time=5),
                    validator=lambda path: (_ for _ in ()).throw(ValueError('invalid')),
                )
            self.assertEqual(destination.read_bytes(), b'known-good')
            self.assertFalse(destination.with_suffix('.opk.tmp').exists())

    def test_download_file_retries_and_atomically_replaces_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'plugin.opk'
            attempts = 0

            def fake_urlopen(request, timeout):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError('temporary failure')
                return self.make_response([b'new-data'])

            with (
                mock.patch('download_plugins.urllib.request.urlopen', side_effect=fake_urlopen),
                mock.patch('download_plugins.time.sleep'),
            ):
                result = download_plugins.download_file(
                    'https://example.com/plugin.opk', destination, retries=2,
                    budgets=small_budgets(time=5),
                    validator=lambda path: {'namespace': 'demo'})

            self.assertEqual(destination.read_bytes(), b'new-data')
            self.assertEqual(result, {'namespace': 'demo'})
            self.assertEqual(attempts, 2)
            self.assertFalse(destination.with_suffix('.opk.tmp').exists())


def _blocking_probe(job, queue_ref):
    time.sleep(60)
    queue_ref.put({'ok': True})


def _crashing_probe(job, queue_ref):
    os._exit(5)


def _resource_probe(job, queue_ref):
    # 模拟 worker 内部把 MemoryError 归类为资源失败并回传有界结果。
    queue_ref.put({'ok': False, 'category': 'resource',
                   'message': 'worker ran out of address space'})


def _stubborn_probe(job, queue_ref):
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)


class WorkerWatchdogTests(unittest.TestCase):
    def test_watchdog_terminates_blocked_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / 'staging'
            staging.mkdir()
            job = {'staging_dir': str(staging), 'budgets': {'time': 0.3}}
            started = time.monotonic()
            with mock.patch.object(download_plugins, 'WORKER_GRACE_PERIOD', 0.2):
                with self.assertRaisesRegex(download_plugins.AssetError, 'budget and was terminated'):
                    download_plugins._spawn_worker(_blocking_probe, job)
            self.assertLess(time.monotonic() - started, 30)

    def test_watchdog_kills_worker_that_ignores_termination(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = {'staging_dir': tmp, 'budgets': {'time': 0.3}}
            with mock.patch.object(download_plugins, 'WORKER_GRACE_PERIOD', 0.2):
                with self.assertRaises(download_plugins.AssetError):
                    download_plugins._spawn_worker(_stubborn_probe, job)

    def test_worker_exit_without_result_surfaces_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = {'staging_dir': tmp, 'budgets': {'time': 10}}
            with self.assertRaisesRegex(download_plugins.AssetError, 'exit code 5'):
                download_plugins._run_asset_job(job, target=_crashing_probe)

    def test_worker_resource_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = {'staging_dir': tmp, 'budgets': {'time': 10}}
            with self.assertRaisesRegex(download_plugins.AssetError, 'resource'):
                download_plugins._run_asset_job(job, target=_resource_probe)

    def test_worker_limit_uses_configured_address_space(self):
        # 不在测试进程内真正收紧 RLIMIT_AS：硬上限降低后不可恢复，
        # 在 GitHub runner 等环境会失败。这里验证接线值；
        # 限制的真实应用由 spawn worker 用例覆盖。
        with mock.patch.object(download_plugins.resource, 'setrlimit') as setter:
            download_plugins._apply_worker_limits({'address_space': 256 * 1024**2})
            setter.assert_called_once_with(
                __import__('resource').RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))

    def test_asset_worker_reports_and_cleans_without_committing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / 'staging'
            staging.mkdir()
            missing = root / 'missing.opk'
            job = {'source': str(missing), 'kind': 'opk', 'staging_dir': str(staging),
                   'budgets': small_budgets()}
            result_queue = queue.Queue()
            download_plugins._asset_worker(job, result_queue)
            result = result_queue.get(timeout=5)
            self.assertFalse(result['ok'])
            self.assertEqual(result['category'], 'internal')
            self.assertFalse((staging / 'payload.bin').exists())

    def test_asset_worker_validates_local_plugin_in_restricted_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / 'staging'
            staging.mkdir()
            source = write_opk(root / 'demo.opk', namespace='local-demo', version='9.9')
            job = {'source': str(source), 'kind': 'opk', 'staging_dir': str(staging),
                   'budgets': small_budgets()}
            result_queue = queue.Queue()
            download_plugins._asset_worker(job, result_queue)
            result = result_queue.get(timeout=5)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['namespace'], 'local-demo')
            self.assertEqual(result['version'], '9.9')
            self.assertTrue((staging / 'payload.bin').is_file())

    def test_asset_worker_memory_failure_is_categorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            source = write_opk(staging / 'demo.opk')
            job = {'source': str(source), 'kind': 'opk', 'staging_dir': str(staging),
                   'budgets': small_budgets()}
            result_queue = queue.Queue()
            with mock.patch.object(download_plugins, 'validate_opk', side_effect=MemoryError()):
                download_plugins._asset_worker(job, result_queue)
            result = result_queue.get(timeout=5)
            self.assertEqual(result['category'], 'resource')

    def test_spawned_worker_rejects_oversized_local_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'big.opk'
            source.write_bytes(b'\0' * (2048))
            job = {'source': str(source), 'kind': 'opk',
                   'staging_dir': str(root / 'staging'),
                   'budgets': small_budgets(download=1024)}
            result_queue = multiprocessing.get_context('spawn').Queue()
            process = multiprocessing.get_context('spawn').Process(
                target=download_plugins._asset_worker, args=(job, result_queue), daemon=True)
            process.start()
            result = result_queue.get(timeout=30)
            process.join(timeout=10)
            self.assertFalse(result['ok'])
            self.assertEqual(result['category'], 'asset')


class ChannelSelectionTests(unittest.TestCase):
    def release(self, tag, prerelease=False, assets=(('x.opk', 1),), published='2026-01-02T00:00:00Z', **extra):
        return {
            'tag_name': tag,
            'prerelease': prerelease,
            'draft': False,
            'published_at': published,
            'id': len(tag),
            'assets': [{'name': name, 'id': asset_id, 'browser_download_url': f'https://example.com/{name}'}
                       for name, asset_id in assets],
            **extra,
        }

    def decision(self, releases, channel, names=('x.opk',)):
        release, mapping = download_plugins.select_channel_release(releases, list(names), channel, 'owner/repo')
        return release['tag_name'], mapping

    def test_decision_table(self):
        cases = [
            # (channel, releases, expected tag or 'fail')
            ('stable', [self.release('2.0.0'), self.release('2.1.0-beta.1', True)], '2.0.0'),
            ('stable', [self.release('2.1.0-beta.1', True)], 'fail'),
            ('testing', [self.release('2.1.0-beta.1', True)], '2.1.0-beta.1'),
            ('testing', [self.release('2.0.0')], '2.0.0'),
            ('testing', [self.release('2.0.0'), self.release('2.0.1-beta.1', True)], '2.0.1-beta.1'),
            ('testing', [self.release('2.0.0'), self.release('1.9.9-beta.1', True)], '2.0.0'),
            ('testing', [self.release('2.0.0'), self.release('2.1.0-beta.1', True)], '2.1.0-beta.1'),
            ('testing', [self.release('2.0.0'), self.release('2.0.0-rc.1', True)], '2.0.0'),
        ]
        for channel, releases, expected in cases:
            with self.subTest(channel=channel, releases=[r['tag_name'] for r in releases]):
                if expected == 'fail':
                    with self.assertRaises(RuntimeError):
                        self.decision(releases, channel)
                else:
                    tag, _ = self.decision(releases, channel)
                    self.assertEqual(tag, expected)

    def test_stable_channel_ignores_prerelease_flag_by_tag_name(self):
        # tag 名像 prerelease 但 API 标志为正式版：按标志归入 S 类。
        releases = [self.release('3.3.7-rc2', prerelease=False)]
        tag, _ = self.decision(releases, 'stable')
        self.assertEqual(tag, '3.3.7-rc2')

    def test_draft_releases_are_excluded(self):
        releases = [self.release('9.9.9', draft=True), self.release('1.0.0')]
        tag, _ = self.decision(releases, 'stable')
        self.assertEqual(tag, '1.0.0')

    def test_releases_without_required_assets_are_not_candidates(self):
        releases = [
            self.release('base-ea1', assets=(('source.tar.gz', 7),)),
            self.release('3.0.18', assets=(('OlivaDiceJoy.opk', 8),)),
        ]
        tag, _ = self.decision(releases, 'stable', names=('OlivaDiceJoy.opk',))
        self.assertEqual(tag, '3.0.18')

    def test_incomplete_newest_release_falls_back_to_older_complete(self):
        releases = [
            self.release('2.2.0', assets=(('x.opk', 1),)),       # 缺 y.opk，不完整
            self.release('2.1.0', assets=(('x.opk', 2), ('y.opk', 3))),
        ]
        tag, mapping = self.decision(releases, 'stable', names=('x.opk', 'y.opk'))
        self.assertEqual(tag, '2.1.0')
        self.assertEqual(set(mapping), {'x.opk', 'y.opk'})

    def test_single_zip_cannot_serve_two_plugins(self):
        releases = [
            self.release('2.2.0', assets=(('bundle.zip', 1),)),  # 一个 ZIP 被两个名字命中
            self.release('2.1.0', assets=(('x.opk', 2), ('y.opk', 3))),
        ]
        tag, mapping = self.decision(releases, 'stable', names=('x.opk', 'y.opk'))
        self.assertEqual(tag, '2.1.0')

    def test_no_complete_release_fails_with_repo_and_channel(self):
        releases = [self.release('2.2.0', assets=(('other.opk', 1),))]
        with self.assertRaisesRegex(
                RuntimeError, r"owner/repo providing \['x\.opk'\] \(channel testing\)"):
            download_plugins.select_channel_release(releases, ['x.opk'], 'testing', 'owner/repo')

    def test_same_category_incomparable_tags_fall_back_to_publish_time(self):
        releases = [
            self.release('v20260920(16)', published='2026-09-20T00:00:00Z'),
            self.release('v26.1001.02', published='2026-10-01T00:00:00Z'),
        ]
        tag, _ = self.decision(releases, 'stable')
        self.assertEqual(tag, 'v26.1001.02')

    def test_incomparable_category_without_publish_time_fails(self):
        releases = [self.release('v20260920(16)', published=''), self.release('3.1.6(1011)', published='')]
        with self.assertRaisesRegex(RuntimeError, 'incomparable'):
            self.decision(releases, 'stable')

    def test_cross_channel_incomparable_tags_fall_back_to_publish_time_with_warning(self):
        # base-ea1（prerelease）与 3.0.18 无法按版本语义比较：经确认的例外，
        # 按有效发布时间兜底并打印告警——2021 年的 prerelease 不应胜过 2026 年的正式版。
        releases = [
            self.release('v20260920(16)', published='2026-09-20T00:00:00Z'),
            self.release('2.0.0-rc.1', prerelease=True, published='2021-11-20T10:41:09Z'),
        ]
        tag, _ = self.decision(releases, 'testing')
        self.assertEqual(tag, 'v20260920(16)')

    def test_cross_channel_incomparable_without_publish_time_still_fails(self):
        releases = [
            self.release('v20260920(16)', published='2026-09-20T00:00:00Z'),
            self.release('2.0.0-rc.1', prerelease=True, published=''),
        ]
        with self.assertRaisesRegex(RuntimeError, 'publish times are missing'):
            self.decision(releases, 'testing')

    def test_cross_channel_incomparable_newer_prerelease_wins_by_publish_time(self):
        releases = [
            self.release('1.0.0', published='2020-01-01T00:00:00Z'),
            self.release('base-ea1', prerelease=True, published='2026-11-01T00:00:00Z'),
        ]
        tag, _ = self.decision(releases, 'testing')
        self.assertEqual(tag, 'base-ea1')

    def test_version_ties_use_publish_time_then_id(self):
        releases = [
            self.release('2.0.0', published='2026-01-01T00:00:00Z'),
            self.release('v2.0.0', published='2026-01-02T00:00:00Z'),
        ]
        tag, _ = self.decision(releases, 'stable')
        self.assertEqual(tag, 'v2.0.0')

    def test_unknown_channel_fails_before_network_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json') as request,
                mock.patch.object(download_plugins, '_run_asset_job') as runner,
                self.assertRaisesRegex(ValueError, 'Unknown plugin channel'),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local', channel='nightly')
            request.assert_not_called()
            runner.assert_not_called()
            self.assertFalse((root / 'plugins').exists())
            self.assertFalse((root / 'manifest.json').exists())


class DownloadFlowTests(unittest.TestCase):
    def test_duplicate_remote_names_fail_before_requests_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/first\n'
                                'demo.opk:https://github.com/owner/second\n')
            output = root / 'plugins'
            manifest = root / 'manifest.json'
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', manifest),
                mock.patch.object(download_plugins, 'request_json') as request,
                mock.patch.object(download_plugins, '_run_asset_job') as runner,
                self.assertRaisesRegex(ValueError, 'Duplicate OPK manifest name'),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            request.assert_not_called()
            runner.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(manifest.exists())

    def test_paged_releases_are_used_instead_of_releases_latest(self):
        names = ['OlivaDiceWebUI.opk', 'OlivaDiceWebUIStandalone.opk']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text(''.join(
                f'{name}:https://github.com/OlivOS-Team/OlivaDiceWebUI/releases\n' for name in names))
            release = {'tag_name': 'v26.1028.01', 'prerelease': False, 'assets': [
                {'name': name, 'id': index,
                 'browser_download_url': f'https://example.com/{name}'}
                for index, name in enumerate(reversed(names))
            ]}

            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value=[release]) as request,
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker(namespace=None)),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            request.assert_called_once()
            self.assertIn('/releases?per_page=100&page=1', request.call_args.args[0])
            self.assertNotIn('/releases/latest', request.call_args.args[0])
            self.assertEqual([item['asset'] for item in result], names)
            self.assertEqual(len({item['namespace'] for item in result}), 2)
            self.assertTrue(all((root / 'plugins' / name).is_file() for name in names))
            self.assertTrue(all(item['prerelease'] is False for item in result))
            self.assertTrue(all(isinstance(item['prerelease'], bool) for item in result))

    def test_pagination_scans_to_short_page_and_budget_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            first_page = [{'tag_name': f'filler-{index}', 'draft': False, 'prerelease': False,
                           'assets': [{'name': 'other.opk', 'id': index}]} for index in range(100)]
            second_page = [{'tag_name': '1.2.3', 'draft': False, 'prerelease': False,
                            'assets': [{'name': 'demo.opk', 'id': 999,
                                        'browser_download_url': 'https://example.com/demo.opk'}]}]
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json',
                                  side_effect=[first_page, second_page]) as request,
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            self.assertEqual(result[0]['version'], '1.2.3')
            self.assertEqual(request.call_count, 2)
            self.assertIn('page=2', request.call_args_list[1].args[0])

            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins2'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest2.json'),
                mock.patch.object(download_plugins, 'request_json',
                                  side_effect=[first_page] * 20),
                self.assertRaisesRegex(RuntimeError, 'pagination budget'),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')

    def test_non_list_release_payload_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value={'message': 'nope'}),
                self.assertRaisesRegex(RuntimeError, 'expected a JSON list'),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')

    def test_testing_channel_uses_prerelease_and_records_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            releases = [
                {'tag_name': '3.5.0', 'draft': False, 'prerelease': False,
                 'published_at': '2026-01-01T00:00:00Z',
                 'assets': [{'name': 'demo.opk', 'id': 1,
                             'browser_download_url': 'https://example.com/demo.opk'}]},
                {'tag_name': '3.5.1-beta.1', 'draft': False, 'prerelease': True,
                 'published_at': '2026-01-02T00:00:00Z',
                 'assets': [{'name': 'demo.opk', 'id': 2,
                             'browser_download_url': 'https://example.com/demo.opk'}]},
            ]
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value=releases),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local',
                                                           channel='testing')
            self.assertEqual(result[0]['version'], '3.5.1-beta.1')
            self.assertTrue(result[0]['prerelease'])
            # stable 渠道对同一仓库仍取正式版。
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value=releases),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
            ):
                stable = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            self.assertEqual(stable[0]['version'], '3.5.0')
            self.assertFalse(stable[0]['prerelease'])

    def test_testing_fallback_to_stable_records_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            releases = [
                {'tag_name': '0.2.24', 'draft': False, 'prerelease': False,
                 'published_at': '2026-01-01T00:00:00Z',
                 'assets': [{'name': 'demo.opk', 'id': 1,
                             'browser_download_url': 'https://example.com/demo.opk'}]},
                {'tag_name': '0.2.21-alpha.1', 'draft': False, 'prerelease': True,
                 'published_at': '2025-12-01T00:00:00Z',
                 'assets': [{'name': 'demo.opk', 'id': 2}]},
            ]
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value=releases),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local',
                                                           channel='testing')
            self.assertEqual(result[0]['version'], '0.2.24')
            self.assertFalse(result[0]['prerelease'])

    def test_worker_failure_keeps_old_files_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'plugins'
            manifest = root / 'manifest.json'
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            release = {'tag_name': '1.0.0', 'prerelease': False, 'assets': [
                {'name': 'demo.opk', 'id': 1, 'browser_download_url': 'https://example.com/demo.opk'}]}
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', manifest),
                mock.patch.object(download_plugins, 'request_json', return_value=[release]),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            good = (output / 'demo.opk').read_bytes()
            manifest.unlink()
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', manifest),
                mock.patch.object(download_plugins, 'request_json', return_value=[release]),
                mock.patch.object(download_plugins, '_run_asset_job',
                                  side_effect=fake_worker(fail=True)),
                self.assertRaisesRegex(download_plugins.AssetError, 'fake worker failure'),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            self.assertEqual((output / 'demo.opk').read_bytes(), good)
            self.assertFalse(manifest.exists())
            self.assertEqual(list(output.glob('.staging-*')), [])

    def test_total_budget_rejects_cumulative_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n'
                                'demo2.opk:https://github.com/owner/repo2\n')
            release = {'tag_name': '1.0.0', 'prerelease': False, 'assets': [
                {'name': 'demo.opk', 'id': 1, 'browser_download_url': 'https://example.com/demo.opk'}]}
            release2 = {'tag_name': '1.0.0', 'prerelease': False, 'assets': [
                {'name': 'demo2.opk', 'id': 2, 'browser_download_url': 'https://example.com/demo2.opk'}]}

            def request_side_effect(url, token=''):
                return [release2] if 'repo2' in url else [release]

            def runner(job):
                payload = Path(job['staging_dir']) / 'payload.bin'
                payload.write_bytes(b'\0' * 3000)
                return {'ok': True, 'namespace': 'demo', 'version': '1.0'}

            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', side_effect=request_side_effect),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=runner),
                mock.patch.object(download_plugins, 'MAX_TOTAL_PLUGIN_BYTES', 4096),
                self.assertRaisesRegex(download_plugins.AssetError, 'total budget'),
            ):
                download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            self.assertFalse((root / 'manifest.json').exists())

    def test_local_override_manifest_matches_installed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / 'local'
            local.mkdir()
            source = write_opk(local / 'demo.opk', namespace='demo', version='local')
            opk_list = root / 'opk.txt'
            opk_list.write_text('demo.opk:https://github.com/owner/repo\n')
            output = root / 'installed'
            manifest = root / 'manifest.json'
            release = {'tag_name': 'remote', 'prerelease': False, 'assets': [
                {'name': 'demo.opk', 'id': 5, 'browser_download_url': 'https://example.com/demo.opk'}
            ]}

            def runner(job):
                if job.get('url'):
                    write_opk(Path(job['staging_dir']) / 'payload.bin', namespace='remote')
                    return {'ok': True, 'namespace': 'remote', 'version': ''}
                (Path(job['staging_dir']) / 'payload.bin').write_bytes(source.read_bytes())
                return {'ok': True, 'namespace': 'demo', 'version': 'local'}

            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', manifest),
                mock.patch.object(download_plugins, 'request_json', return_value=[release]),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=runner),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=local)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['source'], 'local')
                self.assertEqual(result[0]['version'], 'local')
                self.assertFalse(result[0]['prerelease'])
                self.assertEqual(result[0]['sha256'], hashlib.sha256((output / 'demo.opk').read_bytes()).hexdigest())
                self.assertEqual(json.loads(manifest.read_text()), result)
                manifest.unlink()
                with mock.patch.object(download_plugins, '_run_asset_job',
                                       side_effect=fake_worker(fail=True)):
                    with self.assertRaises(download_plugins.AssetError):
                        download_plugins.download_plugins(opk_list, local_dir=local)
                self.assertFalse(manifest.exists())
                self.assertEqual(list(output.glob('.staging-*')), [])

    def test_bom_prefixed_manifest_file_is_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'plugins'
            manifest = root / 'manifest.json'
            release = {'tag_name': '1.0.0', 'prerelease': False, 'assets': [
                {'name': 'demo.opk', 'id': 1, 'browser_download_url': 'https://example.com/demo.opk'}]}
            with self.subTest(case='leading BOM'):
                opk_list = root / 'opk.txt'
                opk_list.write_bytes('\ufeff'.encode('utf-8') +
                                     b'demo.opk:https://github.com/owner/repo\n')
                with (
                    mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                    mock.patch.object(download_plugins, 'MANIFEST_PATH', manifest),
                    mock.patch.object(download_plugins, 'request_json', return_value=[release]) as request,
                    mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
                ):
                    result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
                self.assertEqual(result[0]['name'], 'demo.opk')
                self.assertEqual(result[0]['asset'], 'demo.opk')
            with self.subTest(case='BOM-only file'):
                opk_list = root / 'opk-bom-only.txt'
                opk_list.write_bytes('\ufeff'.encode('utf-8'))
                with (
                    mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                    mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest2.json'),
                    mock.patch.object(download_plugins, 'request_json') as request,
                    mock.patch.object(download_plugins, '_run_asset_job') as runner,
                ):
                    result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
                self.assertEqual(result, [])
                request.assert_not_called()
                runner.assert_not_called()
            with self.subTest(case='mid-file BOM'):
                opk_list = root / 'opk-mid.txt'
                opk_list.write_bytes(
                    b'first.opk:https://github.com/owner/repo\n'
                    + '\ufeff'.encode('utf-8')
                    + b'demo.opk:https://github.com/owner/repo\n')
                try:
                    with (
                        mock.patch.object(download_plugins, 'PLUGIN_DIR', output),
                        mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest3.json'),
                        mock.patch.object(download_plugins, 'request_json') as request,
                        mock.patch.object(download_plugins, '_run_asset_job') as runner,
                    ):
                        download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
                    raise AssertionError('mid-file BOM should fail before any request')
                except ValueError as exc:
                    self.assertIn('U+FEFF', str(exc))
                request.assert_not_called()
                runner.assert_not_called()

    def test_crlf_manifest_still_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opk_list = root / 'opk.txt'
            opk_list.write_bytes(b'demo.opk:https://github.com/owner/repo\r\n')
            release = {'tag_name': '1.0.0', 'prerelease': False, 'assets': [
                {'name': 'demo.opk', 'id': 1, 'browser_download_url': 'https://example.com/demo.opk'}]}
            with (
                mock.patch.object(download_plugins, 'PLUGIN_DIR', root / 'plugins'),
                mock.patch.object(download_plugins, 'MANIFEST_PATH', root / 'manifest.json'),
                mock.patch.object(download_plugins, 'request_json', return_value=[release]),
                mock.patch.object(download_plugins, '_run_asset_job', side_effect=fake_worker()),
            ):
                result = download_plugins.download_plugins(opk_list, local_dir=root / 'no-local')
            self.assertEqual(result[0]['name'], 'demo.opk')


if __name__ == '__main__':
    unittest.main()
