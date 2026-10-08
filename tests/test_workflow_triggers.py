import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowTriggerTests(unittest.TestCase):
    def test_only_build_workflow_remains(self):
        workflows = {path.name for path in WORKFLOWS.iterdir() if path.is_file()}
        self.assertEqual(workflows, {"build.yml"})

    def test_workflows_have_no_schedule_or_cron(self):
        for path in WORKFLOWS.iterdir():
            if path.suffix in {".yml", ".yaml"}:
                with self.subTest(workflow=path.name):
                    self.assertNotRegex(
                        path.read_text(encoding="utf-8"),
                        r"(?m)^\s*(?:schedule|cron)\s*:",
                    )

    def test_build_has_only_manual_trigger(self):
        workflow = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
        trigger_block = re.search(r"(?ms)^on:\n(.*?)(?=^\S|\Z)", workflow)
        self.assertIsNotNone(trigger_block)
        triggers = re.findall(r"(?m)^  ([\w-]+):", trigger_block.group(1))
        self.assertEqual(triggers, ["workflow_dispatch"])

    def test_force_channel_choices_are_preserved(self):
        workflow = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
        dispatch = re.search(
            r"(?ms)^  workflow_dispatch:\n(.*?)(?=^\S|^  \S|\Z)", workflow
        )
        self.assertIsNotNone(dispatch)
        inputs = dispatch.group(1)
        self.assertRegex(inputs, r"(?m)^    inputs:\s*$")
        self.assertEqual(re.findall(r"(?m)^      ([\w-]+):", inputs), ["force_channel"])
        for setting in ("required: false", "type: choice", "default: none", "options:"):
            self.assertRegex(inputs, rf"(?m)^        {re.escape(setting)}\s*$")
        choices = re.findall(r"(?m)^          - (\w+)\s*$", inputs)
        self.assertEqual(choices, ["none", "stable", "testing", "both"])

    def test_retired_workflows_and_marker_are_absent(self):
        for relative in (
            ".github/workflows/keepalive.yml",
            ".github/workflows/cleanup-workflow-runs.yml",
            ".github/last-keepalive",
        ):
            with self.subTest(path=relative):
                self.assertFalse((ROOT / relative).exists())


if __name__ == "__main__":
    unittest.main()
