import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_metadata


class VersionComparisonTests(unittest.TestCase):
    def test_prerelease_aliases_share_rank_and_compare_numbers(self):
        for label, alias in (("alpha", "a"), ("beta", "b")):
            with self.subTest(label=label):
                self.assertEqual(build_metadata.version_key(f"1.0-{label}.1"),
                                 build_metadata.version_key(f"1.0-{alias}.1"))
                self.assertLess(build_metadata.version_key(f"1.0-{label}.2"),
                                build_metadata.version_key(f"1.0-{alias}.10"))
        self.assertLess(build_metadata.version_key("1.0-custom.1"),
                        build_metadata.version_key("1.0-dev.1"))
        self.assertLess(build_metadata.version_key("1.0-42"),
                        build_metadata.version_key("1.0-custom.1"))

    def test_unknown_prerelease_labels_compare_numbers_without_mixed_types(self):
        self.assertLess(build_metadata.version_key("1.0-preview.2"),
                        build_metadata.version_key("1.0-preview.10"))
        self.assertLess(build_metadata.version_key("1.0-preview"),
                        build_metadata.version_key("1.0-preview.10"))
        structured = build_metadata.version_key("1.0-preview.10")
        for suffix in ("preview.10-extra", "42", "custom.anything", "preview"):
            with self.subTest(suffix=suffix):
                unstructured = build_metadata.version_key(f"1.0-{suffix}")
                self.assertEqual(len(structured[1]), len(unstructured[1]))
                self.assertEqual(tuple(map(type, structured[1])), tuple(map(type, unstructured[1])))
                self.assertNotEqual(structured, unstructured)
                self.assertEqual(structured < unstructured, unstructured > structured)

    def test_legacy_naive_time_is_interpreted_as_beijing_time(self):
        self.assertEqual(build_metadata.parse_time("2026-01-02 08:00:00"),
                         build_metadata.parse_time("2026-01-02T00:00:00Z"))
        self.assertFalse(build_metadata.time_newer("2026-01-02T00:00:00Z", "2026-01-02 08:00:00"))
        self.assertTrue(build_metadata.time_newer("2026-01-02T00:00:01Z", "2026-01-02 08:00:00"))
        self.assertTrue(build_metadata.time_newer("2026-01-02 08:00:01", "2026-01-02T00:00:00Z"))

    def test_final_release_is_newer_than_prerelease(self):
        self.assertGreater(
            build_metadata.version_key("0.11.90"),
            build_metadata.version_key("0.11.90-rc.1"),
        )

    def test_prerelease_order_is_alpha_beta_rc(self):
        self.assertLess(
            build_metadata.version_key("0.11.90-alpha.2"),
            build_metadata.version_key("0.11.90-beta.1"),
        )
        self.assertLess(
            build_metadata.version_key("0.11.90-beta.1"),
            build_metadata.version_key("0.11.90-rc.1"),
        )

    def test_channel_needs_build_when_remote_version_is_newer(self):
        record = {
            "stable": {
                "olivos_version": "0.11.80",
                "olivos_published_at": "2026-01-01 08:00:00 +0800",
                "plugins": [],
            },
            "testing": {
                "olivos_version": "0.11.80-rc.1",
                "olivos_published_at": "2026-01-01 08:00:00 +0800",
                "plugins": [],
            },
        }

        remote = {"raw_version": "0.11.81", "published_at": "2026-01-02 08:00:00 +0800"}
        testing = {"raw_version": "0.11.81-rc.2", "published_at": "2026-01-02 08:00:00 +0800"}

        self.assertTrue(build_metadata.olivos_changed(record, "stable", remote))
        self.assertTrue(build_metadata.olivos_changed(record, "testing", testing))

    def test_channel_skips_when_remote_version_is_not_newer(self):
        record = {
            "stable": {
                "olivos_version": "0.11.81",
                "olivos_published_at": "2026-01-02 08:00:00 +0800",
                "plugins": [],
            },
            "testing": {
                "olivos_version": "0.11.81-rc.2",
                "olivos_published_at": "2026-01-02 08:00:00 +0800",
                "plugins": [],
            },
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
    def test_plugin_updates_do_not_trigger_build(self):
        releases = [
            {"tag_name": "0.11.81", "draft": False, "prerelease": False, "published_at": "2026-01-02T00:00:00Z"},
            {"tag_name": "0.11.81-rc.1", "draft": False, "prerelease": True, "published_at": "2026-01-02T00:00:00Z"},
        ]
        record = {
            "stable": {"olivos_version": "0.11.81", "olivos_published_at": "2026-01-02 08:00:00 +0800"},
            "testing": {"olivos_version": "0.11.81-rc.1", "olivos_published_at": "2026-01-02 08:00:00 +0800"},
        }
        with (
            mock.patch("scripts.build_metadata.request_json", return_value=releases) as request,
            mock.patch("scripts.build_metadata.load_record", return_value=record),
        ):
            outputs = build_metadata.detect("ignored.json")

        self.assertEqual(outputs["stable_should_build"], "false")
        self.assertEqual(outputs["testing_should_build"], "false")
        request.assert_called_once_with(f"{build_metadata.RELEASES_API}?per_page=100&page=1", "")
        self.assertFalse(any("full_only" in key for key in outputs))

    def test_core_detection_only_queries_core_release_api(self):
        releases = [{"tag_name": "0.11.82", "prerelease": False, "draft": False}]
        with mock.patch("scripts.build_metadata.request_json", return_value=releases) as request:
            with mock.patch("scripts.build_metadata.load_record", return_value={}):
                outputs = build_metadata.detect("ignored.json")
        self.assertEqual(outputs["stable_should_build"], "true")
        request.assert_called_once_with(f"{build_metadata.RELEASES_API}?per_page=100&page=1", "")


class RecordUpdateTests(unittest.TestCase):
    def test_missing_manifest_does_not_write_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "record.json"
            with self.assertRaises(FileNotFoundError):
                build_metadata.update_record(record, "stable", "1.0", "", Path(tmp) / "missing.json")
            self.assertFalse(record.exists())

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


class NetworkRequestTests(unittest.TestCase):
    def test_fetch_releases_includes_channel_from_second_page(self):
        testing = {"tag_name": "0.11.90-rc.1", "prerelease": True, "draft": False}
        stable = {"tag_name": "0.11.81", "prerelease": False, "draft": False}
        with mock.patch("scripts.build_metadata.request_json", side_effect=[[testing] * 100, [stable]]) as request:
            releases = build_metadata.fetch_releases("token")
        self.assertEqual(len(releases), 101)
        selected = build_metadata.select_latest_releases(releases)
        self.assertEqual(selected["stable"]["raw_version"], "0.11.81")
        self.assertEqual(selected["testing"]["raw_version"], "0.11.90-rc.1")
        self.assertEqual(request.call_args_list, [
            mock.call(f"{build_metadata.RELEASES_API}?per_page=100&page=1", "token"),
            mock.call(f"{build_metadata.RELEASES_API}?per_page=100&page=2", "token"),
        ])

    def test_fetch_releases_stops_at_empty_page(self):
        with mock.patch("scripts.build_metadata.request_json", side_effect=[[{}] * 100, []]) as request:
            self.assertEqual(len(build_metadata.fetch_releases("")), 100)
        self.assertEqual(request.call_count, 2)
        with mock.patch("scripts.build_metadata.request_json", return_value=[]) as request:
            self.assertEqual(build_metadata.fetch_releases(""), [])
        self.assertEqual(request.call_count, 1)

    def test_request_json_retries_temporary_network_failure(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with (
            mock.patch(
                "scripts.build_metadata.urllib.request.urlopen",
                side_effect=[OSError("temporary"), response],
            ) as urlopen,
            mock.patch("scripts.build_metadata.time.sleep"),
        ):
            result = build_metadata.request_json("https://example.com/data", retries=2)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
