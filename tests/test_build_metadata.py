import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_metadata


class VersionComparisonTests(unittest.TestCase):
    def test_channel_needs_build_when_remote_version_is_newer(self):
        record = {
            "stable": {"olivos_version": "0.11.80", "olivos_published_at": "2026-01-01 08:00:00 +0800", "plugins": []},
            "testing": {"olivos_version": "0.11.80-rc.1", "olivos_published_at": "2026-01-01 08:00:00 +0800", "plugins": []},
        }

        remote = {"raw_version": "0.11.81", "published_at": "2026-01-02 08:00:00 +0800"}
        testing = {"raw_version": "0.11.81-rc.2", "published_at": "2026-01-02 08:00:00 +0800"}

        self.assertTrue(build_metadata.olivos_changed(record, "stable", remote))
        self.assertTrue(build_metadata.olivos_changed(record, "testing", testing))

    def test_channel_skips_when_remote_version_is_not_newer(self):
        record = {
            "stable": {"olivos_version": "0.11.81", "olivos_published_at": "2026-01-02 08:00:00 +0800", "plugins": []},
            "testing": {"olivos_version": "0.11.81-rc.2", "olivos_published_at": "2026-01-02 08:00:00 +0800", "plugins": []},
        }

        same = {"raw_version": "0.11.81", "published_at": "2026-01-02 08:00:00 +0800"}
        older = {"raw_version": "0.11.80", "published_at": "2026-01-01 08:00:00 +0800"}
        older_testing = {"raw_version": "0.11.81-rc.1", "published_at": "2026-01-01 08:00:00 +0800"}

        self.assertFalse(build_metadata.olivos_changed(record, "stable", same))
        self.assertFalse(build_metadata.olivos_changed(record, "stable", older))
        self.assertFalse(build_metadata.olivos_changed(record, "testing", older_testing))

    def test_missing_channel_version_needs_build(self):
        remote = {"raw_version": "0.11.81", "published_at": "2026-01-02 08:00:00 +0800"}
        self.assertTrue(build_metadata.olivos_changed({}, "stable", remote))

    def test_newer_publish_time_needs_build_even_when_version_matches(self):
        record = {
            "stable": {
                "olivos_version": "0.11.81",
                "olivos_published_at": "2026-01-02 08:00:00 +0800",
                "plugins": [],
            }
        }
        remote = {"raw_version": "0.11.81", "published_at": "2026-01-03 08:00:00 +0800"}

        self.assertTrue(build_metadata.olivos_changed(record, "stable", remote))


class PluginComparisonTests(unittest.TestCase):
    def test_plugin_update_needs_full_build(self):
        record = {
            "stable": {
                "plugins": [
                    {
                        "name": "OlivaDiceCore.opk",
                        "version": "3.4.52",
                        "published_at": "2026-01-01 08:00:00 +0800",
                    }
                ]
            }
        }
        plugins = [
            {
                "name": "OlivaDiceCore.opk",
                "version": "3.4.53",
                "published_at": "2026-01-02 08:00:00 +0800",
            }
        ]

        self.assertTrue(build_metadata.plugins_changed(record, "stable", plugins))

    def test_same_plugin_versions_and_times_skip(self):
        plugins = [
            {
                "name": "OlivaDiceCore.opk",
                "version": "3.4.53",
                "published_at": "2026-01-02 08:00:00 +0800",
            }
        ]
        record = {"stable": {"plugins": plugins}}

        self.assertFalse(build_metadata.plugins_changed(record, "stable", plugins))


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
                    "published_at": "2026-01-02 08:00:00 +0800",
                    "asset": "OlivaDiceCore.opk",
                }
            ]
            plugins_path.write_text(json.dumps(plugins), encoding="utf-8")

            build_metadata.update_record(
                record_path,
                "stable",
                "0.11.81",
                "2026-01-03 08:00:00 +0800",
                plugins_path,
            )

            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(data["stable"]["olivos_version"], "0.11.81")
            self.assertEqual(data["stable"]["olivos_published_at"], "2026-01-03 08:00:00 +0800")
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
                record_path,
                "stable",
                "0.11.81",
                "2026-01-03 08:00:00 +0800",
                plugins_path,
                force=True,
            )

            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(data["stable"]["olivos_version"], "0.11.80")
            self.assertEqual(data["stable"]["updated_at"], "old")


class ReleaseSelectionTests(unittest.TestCase):
    def test_selects_latest_stable_and_testing_releases(self):
        releases = [
            {"tag_name": "0.11.80", "draft": False, "prerelease": False},
            {"tag_name": "0.11.82-rc.1", "draft": False, "prerelease": True, "published_at": "2026-01-02T00:00:00Z"},
            {"tag_name": "0.11.81", "draft": False, "prerelease": False, "published_at": "2026-01-01T00:00:00Z"},
            {"tag_name": "0.11.83-rc.1", "draft": True, "prerelease": True},
        ]

        selected = build_metadata.select_latest_releases(releases)

        self.assertEqual(selected["stable"]["raw_version"], "0.11.81")
        self.assertEqual(selected["stable"]["docker_tag"], "v0.11.81")
        self.assertEqual(selected["stable"]["published_at"], "2026-01-01 08:00:00 +0800")
        self.assertEqual(selected["testing"]["raw_version"], "0.11.82-rc.1")
        self.assertEqual(selected["testing"]["docker_tag"], "v0.11.82-rc.1")
        self.assertEqual(selected["testing"]["published_at"], "2026-01-02 08:00:00 +0800")


if __name__ == "__main__":
    unittest.main()
