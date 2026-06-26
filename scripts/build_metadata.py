#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

RELEASES_API = "https://api.github.com/repos/OlivOS-Team/OlivOS/releases"
CHANNELS = ("stable", "testing")
BEIJING_TZ = timezone(timedelta(hours=8))


def normalize_version(version):
    return (version or "").strip().removeprefix("v")


def version_key(version):
    version = normalize_version(version)
    parts = re.split(r"([0-9]+|[A-Za-z]+)", version)
    key = []
    for part in parts:
        if not part or part in ".-_+":
            continue
        if part.isdigit():
            key.append((1, int(part)))
        else:
            key.append((0, part.lower()))
    return key


def parse_time(value):
    if not value:
        return None
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def beijing_time(value=None):
    dt = parse_time(value) if value else datetime.now(BEIJING_TZ)
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


def plugin_key(plugin):
    return plugin.get("name") or plugin.get("repo") or plugin.get("asset") or ""


def plugins_changed(record, channel, remote_plugins):
    current_plugins = {
        plugin_key(plugin): plugin
        for plugin in record.get(channel, {}).get("plugins", [])
        if plugin_key(plugin)
    }
    for plugin in remote_plugins:
        key = plugin_key(plugin)
        if not key:
            continue
        current = current_plugins.get(key)
        if current is None:
            return True
        remote_version = normalize_version(plugin.get("version", ""))
        current_version = normalize_version(current.get("version", ""))
        if version_key(remote_version) > version_key(current_version):
            return True
        if version_key(remote_version) == version_key(current_version) and time_newer(
            plugin.get("published_at", ""), current.get("published_at", "")
        ):
            return True
    return False


def fetch_releases(token):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "olivos-docker-build",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(RELEASES_API, headers=headers)
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def parse_opk_line(line):
    line = line.strip()
    if not line:
        return None
    sep = "：" if "：" in line else ":"
    name, url = line.split(sep, 1)
    repo = url.strip().replace("https://github.com/", "").rstrip("/").removesuffix("/releases")
    return name.strip(), repo


def fetch_latest_release(repo, token=""):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "olivos-docker-build",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def fetch_plugin_metadata(opk_path="opk.txt", token=""):
    plugins = []
    path = Path(opk_path)
    if not path.exists():
        return plugins
    with path.open(encoding="utf-8") as f:
        for line in f:
            parsed = parse_opk_line(line)
            if parsed is None:
                continue
            name, repo = parsed
            release = fetch_latest_release(repo, token)
            asset_name = ""
            for asset in release.get("assets", []):
                if asset.get("name", "").endswith(".opk"):
                    asset_name = asset["name"]
                    break
            plugins.append(
                {
                    "name": name,
                    "repo": repo,
                    "version": release.get("tag_name") or release.get("name") or "",
                    "published_at": beijing_time(release.get("published_at")),
                    "asset": asset_name,
                }
            )
    return plugins


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


def detect(record_path, force=False, token="", opk_path="opk.txt"):
    releases = fetch_releases(token)
    selected = select_latest_releases(releases)
    remote_plugins = fetch_plugin_metadata(opk_path, token)
    record = load_record(record_path)

    outputs = {}
    any_should_build = False
    for channel in CHANNELS:
        raw_version = selected[channel]["raw_version"]
        docker_tag = selected[channel]["docker_tag"]
        olivos_update = olivos_changed(record, channel, selected[channel])
        plugin_update = plugins_changed(record, channel, remote_plugins)
        should_build = bool(raw_version) and (force or olivos_update or plugin_update)
        full_only = bool(raw_version) and plugin_update and not olivos_update and not force
        any_should_build = any_should_build or should_build
        outputs[f"{channel}_raw_version"] = raw_version
        outputs[f"{channel}_docker_tag"] = docker_tag
        outputs[f"{channel}_published_at"] = selected[channel]["published_at"]
        outputs[f"{channel}_should_build"] = github_bool(should_build)
        outputs[f"{channel}_full_only"] = github_bool(full_only)

    outputs["any_should_build"] = github_bool(any_should_build)
    append_github_output(outputs)

    print(json.dumps({"olivos": selected, "plugins": remote_plugins}, ensure_ascii=False, indent=2))
    return outputs


def load_plugins(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def update_record(record_path, channel, raw_version, published_at, plugins_path, force=False):
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel}")
    if force:
        print("Force build requested; build record will not be updated.")
        return False

    record = load_record(record_path)
    record[channel] = {
        "olivos_version": normalize_version(raw_version),
        "olivos_published_at": published_at,
        "plugins": load_plugins(plugins_path),
        "updated_at": beijing_time(),
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
    detect_parser.add_argument("--opk", default="opk.txt")

    update_parser = subparsers.add_parser("update-record")
    update_parser.add_argument("--record", default="build-record.json")
    update_parser.add_argument("--channel", required=True, choices=CHANNELS)
    update_parser.add_argument("--raw-version", required=True)
    update_parser.add_argument("--published-at", required=True)
    update_parser.add_argument("--plugins", default="downloaded-plugins.json")
    update_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "detect":
        detect(args.record, args.force, args.token, args.opk)
    elif args.command == "update-record":
        update_record(args.record, args.channel, args.raw_version, args.published_at, args.plugins, args.force)
    else:
        parser.error(f"Unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
