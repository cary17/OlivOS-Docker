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
    def plugins_stage_command(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        plugin_stage = dockerfile.split(' AS plugins\n', 1)[1].split('\nFROM ', 1)[0]
        run_block = plugin_stage.split('RUN --mount=type=secret', 1)[1]
        command = run_block.split('set -eu;', 1)[1].split('\n\n', 1)[0]
        return 'set -eu;' + command.replace('\\\n', '\n')

    def test_plugin_build_failure_is_fatal_and_core_skips_download(self):
        command = self.plugins_stage_command()
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, OLIVOS_RAW_VERSION='1.0', PLUGIN_DIR=tmp,
                       PLUGIN_MANIFEST=f'{tmp}/manifest.json', PLUGIN_CHANNEL='stable')
            for build_type, status in [('full', 23), ('core', 0), ('dev', 0)]:
                with self.subTest(build_type=build_type):
                    result = subprocess.run(
                        ['sh', '-c', 'python() { return 23; };\n' + command],
                        env=dict(env, BUILD_TYPE=build_type), capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)
                    if build_type != 'full':
                        self.assertEqual(Path(env['PLUGIN_MANIFEST']).read_text(), '[]\n')

    def test_unknown_plugin_channel_fails_before_downloader(self):
        command = self.plugins_stage_command()
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, OLIVOS_RAW_VERSION='1.0', PLUGIN_DIR=tmp,
                       PLUGIN_MANIFEST=f'{tmp}/manifest.json', PLUGIN_CHANNEL='nightly')
            for build_type in ('full', 'core', 'dev'):
                with self.subTest(build_type=build_type):
                    result = subprocess.run(
                        ['sh', '-c', 'python() { return 23; };\n' + command],
                        env=dict(env, BUILD_TYPE=build_type), capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn('Unknown plugin channel', result.stderr)
                    self.assertFalse(Path(env['PLUGIN_MANIFEST']).exists())

    def test_plugins_stage_consumes_token_secret_and_copies_shared_module(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        plugin_stage = dockerfile.split(' AS plugins\n', 1)[1].split('\nFROM ', 1)[0]
        # secret 只经 mount 进入下载命令；token 不落入 ARG/ENV。
        self.assertIn('--mount=type=secret,id=github_token', plugin_stage)
        self.assertNotIn('ARG github_token', plugin_stage)
        self.assertNotIn('ENV GITHUB_TOKEN', plugin_stage)
        self.assertIn('ARG PLUGIN_CHANNEL=stable', plugin_stage)
        self.assertIn('/run/secrets/github_token', plugin_stage)
        self.assertIn('anonymous API access', plugin_stage)
        # 下载器复用 scripts/build_metadata.py，必须进入构建上下文。
        self.assertIn('COPY scripts/__init__.py scripts/build_metadata.py ./scripts/', plugin_stage)

    def test_dependency_install_matches_build_type(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        builder = dockerfile.split(' AS builder\n', 1)[1].split('\n# ==================== 运行阶段', 1)[0]
        final = dockerfile.split('FROM python:3.11-slim\n', 1)[1]
        # 按镜像类型一次性选择安装目标。
        self.assertIn("pip install --no-cache-dir '.[extend]'", builder)
        self.assertIn("pip install --no-cache-dir '.[dev]'", builder)
        self.assertIn('pip install --no-cache-dir .;', builder)
        self.assertIn("tomllib.load(open('pyproject.toml', 'rb'))", builder)
        self.assertIn("if 'extend' not in extras", builder)
        # 运行层自检仅约束 full，且发生在 push 之前的构建期。
        self.assertIn('if [ "$BUILD_TYPE" = "full" ]; then', final)
        self.assertIn('python -m pip check', final)
        self.assertIn('import yaml, openpyxl, httpx, lxml.etree', final)

    def test_final_image_runs_as_dedicated_non_root_user(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        final = dockerfile.split('FROM python:3.11-slim\n', 1)[1]
        # 固定 UID/GID 的专用用户与可写 HOME。
        self.assertIn('groupadd --gid 10001 olivos', final)
        self.assertIn('useradd --uid 10001 --gid 10001', final)
        self.assertIn('--create-home --home-dir /home/olivos', final)
        self.assertIn('ENV HOME=/home/olivos', final)
        # 镜像内运行时写入位置授权：源码树、内置 OPK 源目录（mv -fT 需要）、HOME。
        self.assertIn('chown -R olivos:olivos /app/OlivOS /opt/olivos/plugins /home/olivos', final)
        # USER 必须在依赖安装、目录准备与入口脚本之后；其后不允许再切回 root。
        after_entrypoint = final.split('RUN chmod +x /entrypoint.sh', 1)[1]
        self.assertIn('USER olivos', after_entrypoint)
        self.assertNotIn('USER root', after_entrypoint)
        self.assertLess(after_entrypoint.index('USER olivos'), after_entrypoint.index('ENTRYPOINT'))

    def test_derived_image_restores_non_root_user(self):
        dockerfile = (ROOT / 'Dockerfile.extra').read_text()
        self.assertIn('ARG IMAGE_USER=olivos', dockerfile)
        self.assertIn('USER ${IMAGE_USER}', dockerfile)
        # root 只用于构建期安装；其后不得再出现 root 身份。
        after_install = dockerfile.split('-r /tmp/requirements-extra.txt', 1)[1]
        self.assertNotIn('USER root', after_install)
        # 旧基础镜像引导：同 UID/GID 创建用户并授权镜像内写入位置。
        self.assertIn('getent passwd olivos', dockerfile)
        self.assertIn('groupadd --gid 10001 olivos', dockerfile)
        self.assertIn('useradd --uid 10001 --gid 10001', dockerfile)
        self.assertIn('chown -R olivos:olivos /app/OlivOS /home/olivos', dockerfile)
        self.assertIn('chown -R olivos:olivos /opt/olivos/plugins', dockerfile)
        self.assertIn('--build-arg IMAGE_USER=root', dockerfile)
        # 入口脚本覆盖与 chmod 必须发生在切换用户之前。
        self.assertLess(dockerfile.index('RUN chmod +x /entrypoint.sh'),
                        dockerfile.index('USER ${IMAGE_USER}'))

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to run the entrypoint as another user')
    def test_entrypoint_works_as_non_root_and_fails_clearly_when_denied(self):
        import pwd
        nobody = pwd.getpwnam('nobody')
        main_code = 'import sys\nprint(sys.argv[1:])\nsys.exit(7)\n'

        def build_tree(app_mode, seed_mode):
            root = Path(tempfile.mkdtemp())
            root.chmod(0o755)
            app = root / 'app'
            app.mkdir()
            (app / 'main.py').write_text(main_code)
            seed = root / 'seed'
            seed.mkdir()
            bundled = seed / 'demo.opk'
            bundled.write_bytes(b'bundled')
            for path in (root, app, seed, bundled):
                os.chown(path, nobody.pw_uid, nobody.pw_gid)
            app.chmod(app_mode)
            seed.chmod(seed_mode)
            return root, app, seed, bundled

        entrypoint = (ROOT / 'entrypoint.sh').read_text()

        def run_as_nobody(app, seed):
            rendered = entrypoint.replace('/app/OlivOS', str(app))
            rendered = rendered.replace('/opt/olivos/plugins', str(seed))
            rendered = f'python() {{ exec {shlex.quote(sys.executable)} "$@"; }};\n' + rendered
            return subprocess.run(
                ['sh', '-c', rendered, 'entrypoint.sh', 'hello'],
                capture_output=True, text=True,
                user=nobody.pw_uid, group=nobody.pw_gid,
            )

        with self.subTest(case='authorized dirs'):
            root, app, seed, bundled = build_tree(0o755, 0o755)
            try:
                result = run_as_nobody(app, seed)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertIn('hello', result.stdout)
                installed = app / 'plugin/app/demo.opk'
                self.assertEqual(installed.read_bytes(), b'bundled')
                self.assertFalse(bundled.exists())
            finally:
                subprocess.run(['chown', '-R', '0:0', str(root)])
                import shutil as _shutil
                _shutil.rmtree(root, ignore_errors=True)

        with self.subTest(case='unwritable app dir fails clearly'):
            root, app, seed, bundled = build_tree(0o755, 0o755)
            # app 目录属主保持 root 且未给 nobody 写权限：mkdir/mv 必须明确失败。
            os.chown(app, 0, 0)
            try:
                result = run_as_nobody(app, seed)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('hello', result.stdout)
                self.assertTrue(bundled.exists())
                self.assertFalse((app / 'plugin/app/demo.opk').exists())
                self.assertIn('mkdir', result.stderr.lower() + result.stdout.lower())
            finally:
                subprocess.run(['chown', '-R', '0:0', str(root)])
                import shutil as _shutil
                _shutil.rmtree(root, ignore_errors=True)

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

    def test_entrypoint_moves_bundled_plugins_over_same_named_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'app'
            app.mkdir()
            seed = root / 'seed'
            seed.mkdir()
            bundled = seed / 'demo.opk'
            bundled.write_bytes(b'bundled')
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
            self.assertFalse(bundled.exists())
            installed.write_bytes(b'user-version')
            self.assertEqual(run().returncode, 7)
            self.assertEqual(installed.read_bytes(), b'user-version')

            unpacked = installed.with_suffix('')
            unpacked.mkdir()
            unrelated = installed.with_name('user-only.opk')
            unrelated.write_bytes(b'user-only')
            bundled.write_bytes(b'new-bundled')
            self.assertEqual(run().returncode, 7)
            self.assertEqual(installed.read_bytes(), b'new-bundled')
            self.assertFalse(bundled.exists())
            self.assertTrue(unpacked.is_dir())
            self.assertEqual(unrelated.read_bytes(), b'user-only')

            target_file = root / 'user-target'
            target_file.write_bytes(b'user-target')
            for target in (root / 'missing-target', target_file, unpacked):
                with self.subTest(target=target):
                    installed.unlink()
                    installed.symlink_to(target)
                    bundled.write_bytes(b'bundled')
                    result = run()
                    self.assertEqual(result.returncode, 7, result.stderr)
                    self.assertFalse(installed.is_symlink())
                    self.assertEqual(installed.read_bytes(), b'bundled')
                    self.assertFalse(bundled.exists())
            self.assertFalse((root / 'missing-target').exists())
            self.assertEqual(target_file.read_bytes(), b'user-target')
            self.assertEqual(list(unpacked.iterdir()), [])

            installed.unlink()
            installed.mkdir()
            bundled.write_bytes(b'bundled')
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('hello', result.stdout)
            self.assertEqual(list(installed.iterdir()), [])
            self.assertTrue(bundled.exists())
            installed.rmdir()
            self.assertEqual(run('mv() { return 31; };\n').returncode, 31)
            bundled.unlink()
            self.assertEqual(run().returncode, 7)
            self.assertFalse(installed.exists())
            seed.rmdir()
            self.assertEqual(run().returncode, 7)


if __name__ == '__main__':
    unittest.main()
