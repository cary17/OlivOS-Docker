import os
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
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

    def test_entrypoint_forwards_signals_to_process_group_and_waits(self):
        child_code = textwrap.dedent('''
            import os, signal, sys, time
            from pathlib import Path
            events = Path(os.environ['SIGNAL_EVENTS'])
            def stop(signum, frame):
                events.joinpath('child').write_text(str(signum))
                time.sleep(0.1)
                sys.exit(0)
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            events.joinpath('ready-child').touch()
            while True:
                time.sleep(0.1)
        ''')
        main_code = textwrap.dedent('''
            import os, signal, subprocess, sys, time
            from pathlib import Path
            events = Path(os.environ['SIGNAL_EVENTS'])
            def stop(signum, frame):
                events.joinpath('main').write_text(str(signum))
                child.wait(timeout=3)
                sys.exit(7)
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            child = subprocess.Popen([sys.executable, '-c', CHILD_CODE])
            events.joinpath('ready-main').write_text(str(os.getpid()))
            while True:
                time.sleep(0.1)
        ''').replace('CHILD_CODE', repr(child_code))
        for signum, startup in ((signal.SIGTERM, False), (signal.SIGINT, False),
                                (signal.SIGTERM, True), (signal.SIGINT, True)):
            with self.subTest(signum=signum, startup=startup), tempfile.TemporaryDirectory() as tmp:
                app = Path(tmp)
                (app / 'main.py').write_text(main_code)
                entrypoint = (ROOT / 'entrypoint.sh').read_text().replace('/app/OlivOS', tmp)
                entrypoint = entrypoint.replace('/opt/olivos/plugins', str(app / 'no-seed'))
                if startup:
                    entrypoint = entrypoint.replace('MAIN_PID=$!', 'sleep 0.3\nMAIN_PID=$!')
                entrypoint = f'python() {{ exec {shlex.quote(sys.executable)} "$@"; }};\n' + entrypoint
                process = subprocess.Popen(['sh', '-c', entrypoint], env=dict(os.environ, SIGNAL_EVENTS=tmp),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           start_new_session=True)
                main_pid = None
                try:
                    deadline = time.monotonic() + 5
                    while not ((app / 'ready-main').exists() and (app / 'ready-child').exists()):
                        self.assertIsNone(process.poll(), 'Entrypoint exited before readiness')
                        self.assertLess(time.monotonic(), deadline, 'Process readiness timed out')
                        time.sleep(0.01)
                    main_pid = int((app / 'ready-main').read_text())
                    self.assertEqual(os.getpgid(main_pid), main_pid)
                    process.send_signal(signum)
                    deadline = time.monotonic() + 2
                    while not (app / 'main').exists():
                        self.assertLess(time.monotonic(), deadline, 'Signal forwarding timed out')
                        time.sleep(0.005)
                    if process.poll() is None:
                        process.send_signal(signum)
                    _, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 7, stderr)
                    self.assertEqual((app / 'main').read_text(), str(signum))
                    self.assertEqual((app / 'child').read_text(), str(signum))
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(main_pid, 0)
                finally:
                    if main_pid is None and (app / 'ready-main').exists():
                        main_pid = int((app / 'ready-main').read_text())
                    for pgid in (main_pid, process.pid):
                        if pgid is not None:
                            try:
                                os.killpg(pgid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    process.communicate(timeout=5)

    def test_entrypoint_cancels_startup_before_signal_reset_and_setsid(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signum=signum), tempfile.TemporaryDirectory() as tmp:
                app = Path(tmp)
                ready = app / 'launcher-ready'
                entrypoint = (ROOT / 'entrypoint.sh').read_text().replace('/app/OlivOS', tmp)
                entrypoint = entrypoint.replace('/opt/olivos/plugins', str(app / 'no-seed'))
                entrypoint = entrypoint.replace('import os, signal, sys;',
                    'import os, signal, sys; from pathlib import Path; '
                    'Path(os.environ["STARTUP_READY"]).write_text(str(os.getpid())); '
                    '__import__("time").sleep(1);')
                entrypoint = f'python() {{ exec {shlex.quote(sys.executable)} "$@"; }};\n' + entrypoint
                process = subprocess.Popen(['sh', '-c', entrypoint], env=dict(os.environ, STARTUP_READY=str(ready)),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           start_new_session=True)
                launcher_pid = None
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists():
                        self.assertIsNone(process.poll(), 'Entrypoint exited before readiness')
                        self.assertLess(time.monotonic(), deadline, 'Launcher readiness timed out')
                        time.sleep(0.005)
                    launcher_pid = int(ready.read_text())
                    self.assertNotEqual(os.getpgid(launcher_pid), launcher_pid)
                    process.send_signal(signum)
                    _, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 143, stderr)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(launcher_pid, 0)
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate(timeout=5)

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
            entrypoint = f'python() {{ exec {shlex.quote(sys.executable)} "$@"; }};\n' + entrypoint

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
