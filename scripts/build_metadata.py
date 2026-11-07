#!/usr/bin/env python3
import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RELEASES_API = "https://api.github.com/repos/OlivOS-Team/OlivOS/releases"
CHANNELS = ("stable", "testing")
BEIJING_TZ = timezone(timedelta(hours=8))
PRERELEASE_ORDER = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "rc": 3}
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
# 请求预算覆盖全部尝试与退避等待；到顶即失败，不无限等待限流恢复。
REQUEST_TIME_BUDGET = 120
RELEASES_PAGE_BUDGET = 10
GITHUB_API_HOST = "api.github.com"


def normalize_version(version):
    return (version or "").strip().removeprefix("v")


def version_key(version):
    version = normalize_version(version)
    main, separator, suffix = version.partition("-")
    release = tuple(int(part) if part.isdigit() else 0 for part in main.split("."))
    if not separator:
        return release, (1,)
    match = re.fullmatch(r"([A-Za-z]+)[.-]?(\d*)", suffix)
    if match:
        label = match.group(1).lower()
        number = int(match.group(2) or 0)
        if label in PRERELEASE_ORDER:
            return release, (0, PRERELEASE_ORDER[label], "", number, "")
        return release, (0, -1, label, number, suffix)
    return release, (0, -1, suffix, 0, suffix)


def parse_time(value):
    if not value:
        return None
    value = value.strip()
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=BEIJING_TZ) if dt.tzinfo is None else dt
    except ValueError:
        return None


def beijing_time(value=None):
    # 只格式化输入；None、空串或不可解析值保持未知（空串），
    # 不再隐式用当前时间冒充发布时间。当前时间只在 update_record 写
    # updated_at 时显式获取。
    dt = parse_time(value)
    if dt is None:
        return ""
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def time_newer(remote, current):
    remote_dt = parse_time(remote)
    current_dt = parse_time(current)
    if remote_dt is None:
        return False
    if current_dt is None:
        return True
    return remote_dt > current_dt


def default_record():
    return {
        "stable": {
            "olivos_version": "",
            "olivos_published_at": "",
            "plugins": [],
            "updated_at": "",
        },
        "testing": {
            "olivos_version": "",
            "olivos_published_at": "",
            "plugins": [],
            "updated_at": "",
        },
    }


def load_record(path):
    record = default_record()
    path = Path(path)
    if path.exists():
        with path.open(encoding="utf-8") as f:
            loaded = json.load(f)
        for channel in CHANNELS:
            if isinstance(loaded.get(channel), dict):
                record[channel].update(loaded[channel])
    return record


def write_record(path, record):
    path = Path(path)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def select_latest_releases(releases):
    selected = {}
    for channel, prerelease in (("stable", False), ("testing", True)):
        candidates = [
            release
            for release in releases
            if not release.get("draft") and bool(release.get("prerelease")) is prerelease
        ]
        if not candidates:
            selected[channel] = {"raw_version": "", "docker_tag": "", "published_at": ""}
            continue
        latest = max(candidates, key=lambda release: version_key(release.get("tag_name", "")))
        raw_version = normalize_version(latest["tag_name"])
        selected[channel] = {
            "raw_version": raw_version,
            "docker_tag": f"v{raw_version}",
            "published_at": beijing_time(latest.get("published_at")),
        }
    return selected


def olivos_changed(record, channel, remote_release):
    remote_version = normalize_version(remote_release.get("raw_version", ""))
    if not remote_version:
        return False
    channel_record = record.get(channel, {})
    current_version = normalize_version(channel_record.get("olivos_version", ""))
    if not current_version:
        return True
    if version_key(remote_version) > version_key(current_version):
        return True
    if version_key(remote_version) < version_key(current_version):
        return False
    return time_newer(remote_release.get("published_at", ""), channel_record.get("olivos_published_at", ""))


class _NoCrossHostAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Strip Authorization when a redirect leaves the original host.

    urllib 默认在跨主机重定向时仍转发全部头（含 Authorization），
    token 只允许用于 GitHub API，因此这里显式剥离。
    build_opener 会因传入子类而跳过默认的 HTTPRedirectHandler。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirect is None:
            return None
        has_auth = any(key.lower() == "authorization" for key in redirect.headers)
        if has_auth and urllib.parse.urlsplit(redirect.full_url).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            stripped = {
                key: value
                for key, value in redirect.headers.items()
                if key.lower() != "authorization"
            }
            redirect = urllib.request.Request(
                redirect.full_url,
                method=redirect.get_method(),
                headers=stripped,
                origin_req_host=req.origin_req_host,
                unverifiable=True,
            )
        return redirect


JSON_OPENER = urllib.request.build_opener(_NoCrossHostAuthRedirect())


def _header_int(headers, name):
    value = headers.get(name) if headers is not None else None
    if value is None:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def _rate_limit_wait(exc):
    """Validated wait demanded by rate limit headers, or None when unusable."""
    retry_after = _header_int(exc.headers, "Retry-After")
    if retry_after is not None:
        return retry_after
    reset = _header_int(exc.headers, "X-RateLimit-Reset")
    if reset is not None:
        return max(0, reset - int(time.time()) + 1)
    return None


def _bounded_sleep(seconds, deadline):
    """Sleep inside the request budget; False when the budget is exhausted."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(max(0.0, min(seconds, remaining)))
    return True


def _request_failure(url, attempts, last_error, note=""):
    if isinstance(last_error, urllib.error.HTTPError):
        category = f"HTTP {last_error.code}"
        detail = str(last_error.reason)
    elif isinstance(last_error, json.JSONDecodeError):
        category, detail = "JSON decode", str(last_error)
    elif isinstance(last_error, UnicodeDecodeError):
        category, detail = "response decode", str(last_error)
    elif isinstance(last_error, http.client.IncompleteRead):
        category, detail = "incomplete response", str(last_error)
    elif last_error is not None:
        category, detail = "transport", str(last_error)
    else:
        category, detail = "transport", "no response"
    raise RuntimeError(
        f"Failed to request {url} after {attempts} attempt(s) "
        f"({category}: {detail}{note})"
    ) from last_error


def request_json(url, token="", retries=REQUEST_RETRIES):
    """Fetch a JSON document with bounded retries, budget, and rate-limit handling.

    临时传输/解码失败与可恢复 5xx 有限重试；429 与带明确限流证据的 403
    按校验过的限流头在预算内等待；401/404/普通权限型 403 尽早失败。
    最终异常保留 URL、尝试次数与错误类别，不包含认证头或响应正文。
    """
    if retries < 1:
        raise ValueError("retries must be positive")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "olivos-docker-build",
    }
    # token 仅用于 GitHub API 元数据请求。
    if token and urllib.parse.urlsplit(url).hostname == GITHUB_API_HOST:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    deadline = time.monotonic() + REQUEST_TIME_BUDGET
    last_error = None
    note = ""
    for attempt in range(retries):
        try:
            with JSON_OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            rate_limited = exc.code == 429 or (
                exc.code == 403 and _header_int(exc.headers, "X-RateLimit-Remaining") == 0
            )
            if rate_limited:
                wait = _rate_limit_wait(exc)
                if wait is None or wait > deadline - time.monotonic():
                    # 限流窗口不可用或超出请求预算：明确失败，不撞限流。
                    note = "; rate limit window exceeds request budget"
                    break
                time.sleep(wait)
            elif exc.code >= 500:
                if not _bounded_sleep(2**attempt, deadline):
                    break
            else:
                # 401、404、普通权限型 403 等尽早失败。
                break
        except (json.JSONDecodeError, UnicodeDecodeError, http.client.IncompleteRead) as exc:
            last_error = exc
            if attempt + 1 < retries and not _bounded_sleep(2**attempt, deadline):
                break
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < retries and not _bounded_sleep(2**attempt, deadline):
                break
    _request_failure(url, attempt + 1, last_error, note)


def fetch_releases(token):
    releases = []
    for page in range(1, RELEASES_PAGE_BUDGET + 1):
        url = f"{RELEASES_API}?per_page=100&page={page}"
        batch = request_json(url, token)
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected release payload from {url}: expected a JSON list")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
    raise RuntimeError(
        f"Release pagination budget ({RELEASES_PAGE_BUDGET} pages) exhausted for {RELEASES_API}"
    )


def github_bool(value):
    return "true" if value else "false"


def append_github_output(outputs):
    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in outputs.items()]
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def detect(record_path, force=False, token=""):
    releases = fetch_releases(token)
    selected = select_latest_releases(releases)
    record = load_record(record_path)

    outputs = {}
    any_should_build = False
    for channel in CHANNELS:
        raw_version = selected[channel]["raw_version"]
        docker_tag = selected[channel]["docker_tag"]
        olivos_update = olivos_changed(record, channel, selected[channel])
        should_build = bool(raw_version) and (force or olivos_update)
        any_should_build = any_should_build or should_build
        outputs[f"{channel}_raw_version"] = raw_version
        outputs[f"{channel}_docker_tag"] = docker_tag
        outputs[f"{channel}_published_at"] = selected[channel]["published_at"]
        outputs[f"{channel}_should_build"] = github_bool(should_build)

    outputs["any_should_build"] = github_bool(any_should_build)
    append_github_output(outputs)

    print(json.dumps({"olivos": selected}, ensure_ascii=False, indent=2))
    return outputs


def load_plugins(path):
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def update_record(record_path, channel, raw_version, published_at, plugins_path, force=False):
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel}")
    if force:
        print("Force build requested; build record will not be updated.")
        return False

    record = load_record(record_path)
    # updated_at 是唯一需要当前时间的位置，在此显式获取并格式化，
    # 不复用只做格式化的 beijing_time()，也不让 parse_time() 接收 datetime。
    updated_at = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    record[channel] = {
        "olivos_version": normalize_version(raw_version),
        "olivos_published_at": published_at,
        "plugins": load_plugins(plugins_path),
        "updated_at": updated_at,
    }
    write_record(record_path, record)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser("detect")
    detect_parser.add_argument("--record", default="build-record.json")
    detect_parser.add_argument("--force", action="store_true")
    detect_parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))

    update_parser = subparsers.add_parser("update-record")
    update_parser.add_argument("--record", default="build-record.json")
    update_parser.add_argument("--channel", required=True, choices=CHANNELS)
    update_parser.add_argument("--raw-version", required=True)
    update_parser.add_argument("--published-at", required=True)
    update_parser.add_argument("--plugins", default="downloaded-plugins.json")
    update_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "detect":
        detect(args.record, args.force, args.token)
    elif args.command == "update-record":
        update_record(args.record, args.channel, args.raw_version, args.published_at, args.plugins, args.force)
    else:
        parser.error(f"Unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
