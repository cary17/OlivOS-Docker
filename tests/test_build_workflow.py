import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/build.yml"


class WorkflowDispatchTests(unittest.TestCase):
    def test_force_selection_executes_and_rejects_missing_releases(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split('      - name: Detect latest OlivOS releases\n', 1)[1]
        command = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        mock_python = '''python3() {
          printf 'stable_raw_version=%s\\ntesting_raw_version=%s\\n' "$STABLE_VERSION" "$TESTING_VERSION" >> "$GITHUB_OUTPUT"
          printf 'stable_should_build=true\\ntesting_should_build=true\\nany_should_build=true\\n' >> "$GITHUB_OUTPUT"
        }
        '''
        for channel in ('none', 'stable', 'testing', 'both'):
            rendered = command.replace('${{ steps.force.outputs.enabled }}', str(channel != 'none').lower())
            rendered = rendered.replace('${{ steps.force.outputs.channel }}', channel)
            for stable, testing in (('1.0', '1.1-rc.1'), ('', '1.1-rc.1'), ('1.0', ''), ('', '')):
                with self.subTest(channel=channel, stable=stable, testing=testing):
                    with tempfile.TemporaryDirectory() as tmp:
                        output = Path(tmp) / 'output'
                        env = dict(os.environ, GITHUB_OUTPUT=str(output), STABLE_VERSION=stable, TESTING_VERSION=testing)
                        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', mock_python + rendered],
                                                env=env, capture_output=True, text=True)
                        missing = (channel in ('stable', 'both') and not stable) or (channel in ('testing', 'both') and not testing)
                        self.assertEqual(result.returncode, 1 if missing else 0, result.stderr)
                        if missing:
                            self.assertIn('No release available', result.stderr)
                            self.assertEqual(output.read_text().count('any_should_build='), 1)
                            continue
                        outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
                        self.assertEqual(outputs['stable_should_build'], str(channel in ('none', 'stable', 'both')).lower())
                        self.assertEqual(outputs['testing_should_build'], str(channel in ('none', 'testing', 'both')).lower())

    def test_force_refresh_and_record_use_image_artifacts(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("no-cache: ${{ needs.check.outputs.force_build == 'true' }}", workflow)
        self.assertIn("pull: ${{ needs.check.outputs.force_build == 'true' }}", workflow)
        self.assertIn('${{ steps.meta.outputs.image }}@${{ steps.image.outputs.digest }}', workflow)
        self.assertIn('cmp plugins-amd64.json plugins-arm64.json', workflow)
        record = workflow.split('\n  record:', 1)[1]
        self.assertNotIn('download_plugins.py', record)
        self.assertIn('actions/download-artifact@v4', record)
        for channel in ('stable', 'testing'):
            self.assertIn(f'--plugins manifests/plugins-{channel}/plugins-amd64.json', record)

    def test_image_verifier_accepts_exact_inventory_and_rejects_corruption(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        code = workflow.split("--entrypoint python \"$PLATFORM_REF\" -c '\n", 1)[1]
        code = textwrap.dedent(code.split("\n          ' >", 1)[0])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / 'demo.opk'
            plugin.write_bytes(b'plugin-data')
            manifest = [{'name': plugin.name, 'prerelease': False,
                         'sha256': hashlib.sha256(plugin.read_bytes()).hexdigest()}]
            (root / 'manifest.json').write_text(json.dumps(manifest))
            code = code.replace('/opt/olivos/plugins', tmp)
            env = dict(os.environ, PLUGIN_CHANNEL='stable')
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), manifest)
            plugin.write_bytes(b'corrupt')
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            plugin.write_bytes(b'plugin-data')
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0)
            plugin.unlink()
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)

    def test_image_verifier_enforces_channel_and_boolean_prerelease(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        code = workflow.split("--entrypoint python \"$PLATFORM_REF\" -c '\n", 1)[1]
        code = textwrap.dedent(code.split("\n          ' >", 1)[0])
        self.assertIn('PLUGIN_CHANNEL', code)

        def verify(manifest, channel):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                plugin = root / 'demo.opk'
                plugin.write_bytes(b'plugin-data')
                for entry in manifest:
                    entry.setdefault('sha256', hashlib.sha256(plugin.read_bytes()).hexdigest())
                (root / 'manifest.json').write_text(json.dumps(manifest))
                code_run = code.replace('/opt/olivos/plugins', tmp)
                return subprocess.run([sys.executable, '-c', code_run], capture_output=True, text=True,
                                      env=dict(os.environ, PLUGIN_CHANNEL=channel))

        remote = {'name': 'demo.opk', 'prerelease': True}
        self.assertEqual(verify([remote], 'stable').returncode, 1)  # stable 不得预装 prerelease
        self.assertEqual(verify([remote], 'testing').returncode, 0)  # testing 允许 prerelease
        fallback = {'name': 'demo.opk', 'prerelease': False}
        self.assertEqual(verify([fallback], 'testing').returncode, 0)  # testing 正式版回退合法
        self.assertEqual(verify([fallback], 'stable').returncode, 0)
        local_entry = {'name': 'demo.opk', 'prerelease': False, 'source': 'local'}
        self.assertEqual(verify([local_entry], 'stable').returncode, 0)  # 渠道约束只针对远程插件
        missing_flag = {'name': 'demo.opk'}
        self.assertNotEqual(verify([missing_flag], 'stable').returncode, 0)
        string_flag = {'name': 'demo.opk', 'prerelease': 'true'}
        self.assertNotEqual(verify([string_flag], 'testing').returncode, 0)

    def test_workflow_permissions_are_minimal(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        top_level = workflow.split('\njobs:', 1)[0]
        self.assertIn('permissions:', top_level)
        self.assertIn('contents: read', top_level)
        self.assertNotIn('attestations: write', workflow)
        self.assertNotIn('id-token: write', workflow)

        check = workflow.split('\n  check:', 1)[1].split('\n  build:', 1)[0]
        self.assertIn('contents: read', check)
        self.assertNotIn('contents: write', check)
        self.assertNotIn('packages: write', check)

        build = workflow.split('\n  build:', 1)[1].split('\n  record:', 1)[0]
        self.assertIn('contents: read', build)
        self.assertIn('packages: write', build)
        self.assertNotIn('contents: write', build)
        self.assertNotIn('attestations: write', build)

        record = workflow.split('\n  record:', 1)[1]
        self.assertIn('contents: write', record)

    def test_build_passes_channel_argument_and_token_secret(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('PLUGIN_CHANNEL=${{ matrix.channel }}', workflow)
        self.assertIn('secrets: |', workflow)
        self.assertIn('github_token=${{ secrets.GITHUB_TOKEN }}', workflow)
        # 渠道必须进入下载步骤的缓存键，使不同渠道不共享插件层缓存。
        self.assertIn('cache-from: type=gha,scope=${{ matrix.channel }}-${{ matrix.build_type }}', workflow)
        self.assertIn('cache-to: type=gha,mode=max,scope=${{ matrix.channel }}-${{ matrix.build_type }}', workflow)
        # token 不允许经由 build-args 传递。
        build_args = workflow.split('build-args: |', 1)[1].split('secrets:', 1)[0]
        self.assertNotIn('GITHUB_TOKEN', build_args)

    def test_each_architecture_uses_its_child_digest(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split('      - name: Verify and collect image plugin manifest\n', 1)[1]
        command = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        index = {'manifests': [
            {'platform': {'os': 'linux', 'architecture': arch}, 'digest': 'sha256:' + char * 64}
            for arch, char in [('arm64', 'b'), ('amd64', 'a')]
        ]}
        mock_docker = '''docker() {
          if [ "$1" = buildx ]; then
            printf '%s' "$INDEX"
          else
            printf '%s\\n' "$*" >> "$CALLS"
            printf '[]\\n'
          fi
        }
        '''
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / 'calls'
            env = dict(os.environ, IMAGE_REF='example.com/olivos@sha256:' + 'c' * 64,
                       INDEX=json.dumps(index), CALLS=str(calls))
            result = subprocess.run(['bash', '-e', '-c', mock_docker + command], cwd=tmp,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            recorded = calls.read_text()
            for arch, char in [('amd64', 'a'), ('arm64', 'b')]:
                self.assertIn(f'--platform linux/{arch} --entrypoint python example.com/olivos@sha256:{char * 64}', recorded)
            self.assertNotIn(env['IMAGE_REF'], recorded)
            for manifests in (index['manifests'][:1], index['manifests'] * 2):
                with self.subTest(manifests=manifests):
                    result = subprocess.run(['bash', '-e', '-c', mock_docker + command], cwd=tmp,
                                            env=dict(env, INDEX=json.dumps({'manifests': manifests})),
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)

if __name__ == "__main__":
    unittest.main()
