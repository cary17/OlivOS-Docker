import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_metadata


class VersionComparisonTests(unittest.TestCase):
    def test_channel_needs_build_when_remote_version_is_newer(self):
        record = {
            "stable": {"olivos_version": "0.11.80", "plugins": []},
            "testing": {"olivos_version": "0.11.80-rc.1", "plugins": []},
        }

        self.assertTrue(build_metadata.needs_build(record, "stable", "0.11.81"))
        self.assertTrue(build_metadata.needs_build(record, "testing", "0.11.81-rc.2"))

    def test_channel_skips_when_remote_version_is_not_newer(self):
        record = {
            "stable": {"olivos_version": "0.11.81", "plugins": []},
            "testing": {"olivos_version": "0.11.81-rc.2", "plugins": []},
        }

        self.assertFalse(build_metadata.needs_build(record, "stable", "0.11.81"))
        self.assertFalse(build_metadata.needs_build(record, "stable", "0.11.80"))
        self.assertFalse(build_metadata.needs_build(record, "testing", "0.11.81-rc.1"))

    def test_missing_channel_version_needs_build(self):
        self.assertTrue(build_metadata.needs_build({}, "stable", "0.11.81"))


class RecordUpdateTests(unittest.TestCase):
    def test_update_record_writes_channel_version_and_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "build-record.json"
            plugins_path = Path(tmp) / "plugins.json"
            plugins = [
                {
                    "name": "OlivaDiceCore.opk",
                    "repo": "OlivOS-Team/OlivaDiceCore",
                    "version": "1.2.3",
                    "asset": "OlivaDiceCore.opk",
                }
            ]
            plugins_path.write_text(json.dumps(plugins), encoding="utf-8")

            build_metadata.update_record(record_path, "stable", "0.11.81", plugins_path)

            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(data["stable"]["olivos_version"], "0.11.81")
            self.assertEqual(data["stable"]["plugins"], plugins)
            self.assertIn("updated_at", data["stable"])
            self.assertEqual(data["testing"]["olivos_version"], "")

    def test_force_build_does_not_update_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "build-record.json"
            record_path.write_text(
                json.dumps(
                    {
                        "stable": {
                            "olivos_version": "0.11.80",
                            "plugins": [],
                            "updated_at": "old",
                        }
                    }
                ),
                encoding="utf-8",
            )
            plugins_path = Path(tmp) / "plugins.json"
            plugins_path.write_text("[]", encoding="utf-8")

            build_metadata.update_record(
                record_path, "stable", "0.11.81", plugins_path, force=True
            )

            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(data["stable"]["olivos_version"], "0.11.80")
            self.assertEqual(data["stable"]["updated_at"], "old")


class ReleaseSelectionTests(unittest.TestCase):
    def test_selects_latest_stable_and_testing_releases(self):
        releases = [
            {"tag_name": "0.11.80", "draft": False, "prerelease": False},
            {"tag_name": "0.11.82-rc.1", "draft": False, "prerelease": True},
            {"tag_name": "0.11.81", "draft": False, "prerelease": False},
            {"tag_name": "0.11.83-rc.1", "draft": True, "prerelease": True},
        ]

        selected = build_metadata.select_latest_releases(releases)

        self.assertEqual(selected["stable"]["raw_version"], "0.11.81")
        self.assertEqual(selected["stable"]["docker_tag"], "v0.11.81")
        self.assertEqual(selected["testing"]["raw_version"], "0.11.82-rc.1")
        self.assertEqual(selected["testing"]["docker_tag"], "v0.11.82-rc.1")


if __name__ == "__main__":
    unittest.main()
