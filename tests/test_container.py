import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContainerTests(unittest.TestCase):
    def test_plugin_build_failure_is_fatal_and_core_skips_download(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        plugin_stage = dockerfile.split(' AS plugins\n', 1)[1].split('\nFROM ', 1)[0]
        command = plugin_stage.split('RUN set -eu;', 1)[1].split('\n\n', 1)[0]
        command = 'set -eu;' + command.replace('\\\n', '\n')
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, OLIVOS_RAW_VERSION='1.0', PLUGIN_DIR=tmp,
                       PLUGIN_MANIFEST=f'{tmp}/manifest.json')
            for build_type, status in [('full', 23), ('core', 0), ('dev', 0)]:
                with self.subTest(build_type=build_type):
                    result = subprocess.run(
                        ['sh', '-c', 'python() { return 23; };\n' + command],
                        env=dict(env, BUILD_TYPE=build_type), capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)
                    if build_type != 'full':
                        self.assertEqual(Path(env['PLUGIN_MANIFEST']).read_text(), '[]\n')

    def test_entrypoint_seeds_missing_plugins_without_overwriting_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'app'
            app.mkdir()
            seed = root / 'seed'
            seed.mkdir()
            (seed / 'demo.opk').write_bytes(b'bundled')
            (app / 'main.py').write_text('import sys\nprint(sys.argv[1:])\nsys.exit(7)\n')
            entrypoint = (ROOT / 'entrypoint.sh').read_text().replace('/app/OlivOS', str(app))
            entrypoint = entrypoint.replace('/opt/olivos/plugins', str(seed))
            entrypoint = f'python() {{ {shlex.quote(sys.executable)} "$@"; }};\n' + entrypoint

            def run(prefix=''):
                return subprocess.run(['sh', '-c', prefix + entrypoint, 'entrypoint.sh', 'hello'],
                                      capture_output=True, text=True)

            result = run()
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn('hello', result.stdout)
            installed = app / 'plugin/app/demo.opk'
            self.assertEqual(installed.read_bytes(), b'bundled')
            installed.write_bytes(b'user-version')
            self.assertEqual(run().returncode, 7)
            self.assertEqual(installed.read_bytes(), b'user-version')

            installed.unlink()
            unpacked = installed.with_suffix('')
            unpacked.mkdir()
            self.assertEqual(run().returncode, 7)
            self.assertFalse(installed.exists())
            unpacked.rmdir()
            installed.symlink_to(root / 'missing-target')
            self.assertEqual(run().returncode, 7)
            self.assertTrue(installed.is_symlink())
            installed.unlink()

            self.assertEqual(run('cp() { return 31; };\n').returncode, 31)
            (seed / 'demo.opk').unlink()
            self.assertEqual(run().returncode, 7)
            self.assertFalse(installed.exists())
            seed.rmdir()
            self.assertEqual(run().returncode, 7)


if __name__ == '__main__':
    unittest.main()
