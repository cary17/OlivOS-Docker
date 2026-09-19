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


if __name__ == "__main__":
    unittest.main()
