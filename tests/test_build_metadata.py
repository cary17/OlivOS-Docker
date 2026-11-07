import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
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

    def test_fetch_releases_rejects_non_list_payload(self):
        with mock.patch("scripts.build_metadata.request_json", return_value={"message": "nope"}):
            with self.assertRaisesRegex(RuntimeError, "expected a JSON list"):
                build_metadata.fetch_releases("")

    def test_fetch_releases_budget_exhaustion_fails_loud(self):
        full_page = [{"tag_name": "filler"}] * 100
        with mock.patch("scripts.build_metadata.request_json", return_value=full_page):
            with self.assertRaisesRegex(RuntimeError, "pagination budget"):
                build_metadata.fetch_releases("")

    def _open_response(self, payload=b'{"ok": true}'):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = payload
        return response

    def test_request_json_retries_temporary_network_failure(self):
        response = self._open_response()
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open",
                              side_effect=[OSError("temporary"), response]) as opener,
            mock.patch("scripts.build_metadata.time.sleep"),
        ):
            result = build_metadata.request_json("https://example.com/data", retries=2)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(opener.call_count, 2)

    def test_request_json_retries_decoding_failures_then_succeeds(self):
        bad = self._open_response(b"{oops")
        good = self._open_response()
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[bad, good]),
            mock.patch("scripts.build_metadata.time.sleep"),
        ):
            self.assertEqual(build_metadata.request_json("https://example.com/data", retries=2), {"ok": True})

    def test_request_json_invalid_encoding_is_retried_with_context(self):
        bad = self._open_response(b"\x80\x81invalid-utf8")
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", return_value=bad),
            mock.patch("scripts.build_metadata.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "https://example.com/data.*attempt.*decode"):
                build_metadata.request_json("https://example.com/data", retries=2)

    def test_request_json_rate_limit_headers_are_validated_and_waited(self):
        import email.message
        headers = email.message.Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = str(int(time.time()) + 1)
        limited = urllib.error.HTTPError("https://api.github.com/x", 403, "rate limited", headers, None)
        good = self._open_response()
        sleeps = []
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[limited, good]),
            mock.patch("scripts.build_metadata.time.sleep", side_effect=sleeps.append),
        ):
            self.assertEqual(build_metadata.request_json("https://api.github.com/x", retries=2), {"ok": True})
        self.assertTrue(sleeps and sleeps[0] <= 5)

    def test_request_json_rate_limit_beyond_budget_fails_without_hour_sleep(self):
        import email.message
        headers = email.message.Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = str(int(time.time()) + 3600)
        limited = urllib.error.HTTPError("https://api.github.com/x", 403, "rate limited", headers, None)
        sleeps = []
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[limited]),
            mock.patch("scripts.build_metadata.time.sleep", side_effect=sleeps.append),
            mock.patch.object(build_metadata, "REQUEST_TIME_BUDGET", 60),
        ):
            with self.assertRaisesRegex(RuntimeError, "rate limit window exceeds request budget"):
                build_metadata.request_json("https://api.github.com/x", retries=3)
        self.assertEqual(sleeps, [])

    def test_request_json_plain_403_is_not_treated_as_rate_limit(self):
        limited = urllib.error.HTTPError("https://api.github.com/x", 403, "forbidden", {}, None)
        with mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[limited]) as opener:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                build_metadata.request_json("https://api.github.com/x", retries=3)
        self.assertEqual(opener.call_count, 1)

    def test_request_json_401_fails_fast(self):
        unauthorized = urllib.error.HTTPError("https://api.github.com/x", 401, "bad credentials", {}, None)
        with mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[unauthorized]) as opener:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                build_metadata.request_json("https://api.github.com/x", retries=3)
        self.assertEqual(opener.call_count, 1)

    def test_request_json_500_is_retried(self):
        busy = urllib.error.HTTPError("https://api.github.com/x", 502, "bad gateway", {}, None)
        good = self._open_response()
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[busy, good]),
            mock.patch("scripts.build_metadata.time.sleep"),
        ):
            self.assertEqual(build_metadata.request_json("https://api.github.com/x", retries=2), {"ok": True})

    def test_request_json_429_uses_valid_retry_after(self):
        import email.message
        headers = email.message.Message()
        headers["Retry-After"] = "0"
        limited = urllib.error.HTTPError("https://api.github.com/x", 429, "slow down", headers, None)
        good = self._open_response()
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[limited, good]),
            mock.patch("scripts.build_metadata.time.sleep"),
        ):
            self.assertEqual(build_metadata.request_json("https://api.github.com/x", retries=2), {"ok": True})

    def test_request_json_429_with_invalid_retry_after_fails(self):
        import email.message
        headers = email.message.Message()
        headers["Retry-After"] = "not-a-number"
        limited = urllib.error.HTTPError("https://api.github.com/x", 429, "slow down", headers, None)
        with (
            mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[limited]),
            mock.patch("scripts.build_metadata.time.sleep") as sleeper,
        ):
            with self.assertRaisesRegex(RuntimeError, "rate limit window exceeds request budget"):
                build_metadata.request_json("https://api.github.com/x", retries=3)
        sleeper.assert_not_called()

    def test_request_json_token_is_not_leaked_in_errors(self):
        unauthorized = urllib.error.HTTPError("https://api.github.com/x", 401, "no", {}, None)
        with mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=[unauthorized]):
            with self.assertRaises(RuntimeError) as caught:
                build_metadata.request_json("https://api.github.com/x", token="super-secret-token")
        message = str(caught.exception)
        self.assertNotIn("super-secret-token", message)
        self.assertNotIn("Bearer", message)

    def test_request_json_token_is_only_used_for_github_api(self):
        captured = []

        def fake_open(request, timeout):
            captured.append(request)
            return self._open_response()

        with mock.patch.object(build_metadata.JSON_OPENER, "open", side_effect=fake_open):
            build_metadata.request_json("https://api.github.com/repos/o/r", token="secret")
            build_metadata.request_json("https://example.com/data", token="secret")
        self.assertTrue(captured[0].has_header("Authorization"))
        self.assertFalse(captured[1].has_header("Authorization"))

    def test_cross_host_redirect_drops_authorization(self):
        handler = build_metadata._NoCrossHostAuthRedirect()
        request = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer secret"})
        same_host = handler.redirect_request(request, None, 302, "Found", {}, "https://api.github.com/y")
        self.assertTrue(same_host.has_header("Authorization"))
        cross_host = handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/y")
        self.assertFalse(cross_host.has_header("Authorization"))


class UnknownPublishTimeTests(unittest.TestCase):
    def test_beijing_time_never_invents_now(self):
        self.assertEqual(build_metadata.beijing_time(None), "")
        self.assertEqual(build_metadata.beijing_time(""), "")
        self.assertEqual(build_metadata.beijing_time("   "), "")
        self.assertEqual(build_metadata.beijing_time("not-a-timestamp"), "")

    def test_beijing_time_formats_known_inputs(self):
        self.assertEqual(build_metadata.beijing_time("2026-01-02T00:00:00Z"), "2026-01-02 08:00:00 +0800")
        self.assertEqual(build_metadata.beijing_time("2026-01-02 08:00:00 +0800"), "2026-01-02 08:00:00 +0800")

    def test_missing_publish_time_stays_empty_through_selection(self):
        releases = [{"tag_name": "0.11.81", "draft": False, "prerelease": False}]
        selected = build_metadata.select_latest_releases(releases)
        self.assertEqual(selected["stable"]["published_at"], "")

    def test_same_version_with_unknown_times_does_not_rebuild(self):
        self.assertFalse(build_metadata.time_newer("", ""))
        self.assertFalse(build_metadata.time_newer(None, ""))
        self.assertFalse(build_metadata.time_newer("garbage", ""))

    def test_valid_remote_time_builds_when_local_is_unknown(self):
        self.assertTrue(build_metadata.time_newer("2026-01-02T00:00:00Z", ""))

    def test_update_record_writes_real_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "build-record.json"
            plugins_path = Path(tmp) / "plugins.json"
            plugins_path.write_text("[]", encoding="utf-8")
            before = build_metadata.parse_time(
                datetime.now(build_metadata.BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %z"))
            build_metadata.update_record(record_path, "stable", "0.11.81", "", plugins_path)
            data = json.loads(record_path.read_text(encoding="utf-8"))
            updated = build_metadata.parse_time(data["stable"]["updated_at"])
            self.assertIsNotNone(updated)
            self.assertLess(abs((updated - before).total_seconds()), 300)
            self.assertEqual(data["stable"]["olivos_published_at"], "")

    def test_old_record_without_new_fields_stays_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_path = Path(tmp) / "build-record.json"
            record_path.write_text(json.dumps({
                "stable": {"olivos_version": "0.11.80", "plugins": [{"name": "old.opk"}]},
            }), encoding="utf-8")
            plugins_path = Path(tmp) / "plugins.json"
            plugins_path.write_text("[]", encoding="utf-8")
            build_metadata.update_record(record_path, "testing", "0.11.81-rc.1", "", plugins_path)
            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(data["stable"]["plugins"], [{"name": "old.opk"}])
            self.assertEqual(data["testing"]["olivos_version"], "0.11.81-rc.1")


if __name__ == "__main__":
    unittest.main()
