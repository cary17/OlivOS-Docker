import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/build.yml"


class WorkflowDispatchTests(unittest.TestCase):
    def test_force_build_can_target_one_channel(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("force_channel:", workflow)
        self.assertIn("type: choice", workflow)
        self.assertIn("- stable", workflow)
        self.assertIn("- testing", workflow)
        self.assertIn('echo "stable_should_build=false" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn('echo "testing_should_build=false" >> "$GITHUB_OUTPUT"', workflow)

    def test_force_refresh_and_record_use_image_artifacts(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn('full_only', workflow)
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
        code = workflow.split("--entrypoint python \"$IMAGE_REF\" -c '\n", 1)[1]
        code = textwrap.dedent(code.split("\n          ' >", 1)[0])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / 'demo.opk'
            plugin.write_bytes(b'plugin-data')
            manifest = [{'name': plugin.name, 'sha256': hashlib.sha256(plugin.read_bytes()).hexdigest()}]
            (root / 'manifest.json').write_text(json.dumps(manifest))
            code = code.replace('/opt/olivos/plugins', tmp)
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), manifest)
            plugin.write_bytes(b'corrupt')
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            plugin.unlink()
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

if __name__ == "__main__":
    unittest.main()
