#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RELEASES_API = "https://api.github.com/repos/OlivOS-Team/OlivOS/releases"
CHANNELS = ("stable", "testing")


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


def default_record():
    return {
        "stable": {"olivos_version": "", "plugins": [], "updated_at": ""},
        "testing": {"olivos_version": "", "plugins": [], "updated_at": ""},
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
            selected[channel] = {"raw_version": "", "docker_tag": ""}
            continue
        latest = max(candidates, key=lambda release: version_key(release.get("tag_name", "")))
        raw_version = normalize_version(latest["tag_name"])
        selected[channel] = {
            "raw_version": raw_version,
            "docker_tag": f"v{raw_version}",
        }
    return selected


def needs_build(record, channel, remote_version):
    remote_version = normalize_version(remote_version)
    if not remote_version:
        return False
    current_version = normalize_version(record.get(channel, {}).get("olivos_version", ""))
    if not current_version:
        return True
    return version_key(remote_version) > version_key(current_version)


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
        should_build = bool(raw_version) and (force or needs_build(record, channel, raw_version))
        any_should_build = any_should_build or should_build
        outputs[f"{channel}_raw_version"] = raw_version
        outputs[f"{channel}_docker_tag"] = docker_tag
        outputs[f"{channel}_should_build"] = github_bool(should_build)

    outputs["any_should_build"] = github_bool(any_should_build)
    append_github_output(outputs)

    print(json.dumps(selected, ensure_ascii=False, indent=2))
    return outputs


def load_plugins(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def update_record(record_path, channel, raw_version, plugins_path, force=False):
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel}")
    if force:
        print("Force build requested; build record will not be updated.")
        return False

    record = load_record(record_path)
    record[channel] = {
        "olivos_version": normalize_version(raw_version),
        "plugins": load_plugins(plugins_path),
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
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
    update_parser.add_argument("--plugins", default="downloaded-plugins.json")
    update_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "detect":
        detect(args.record, args.force, args.token)
    elif args.command == "update-record":
        update_record(args.record, args.channel, args.raw_version, args.plugins, args.force)
    else:
        parser.error(f"Unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
