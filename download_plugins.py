#!/usr/bin/env python3
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

PLUGIN_DIR = os.environ.get("PLUGIN_DIR", "OlivOS/plugin/app")
MANIFEST_PATH = os.environ.get("PLUGIN_MANIFEST", "downloaded-plugins.json")
os.makedirs(PLUGIN_DIR, exist_ok=True)
manifest = []
BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_time(value):
    if not value:
        return ""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %z")

with open("opk.txt", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        sep = "：" if "：" in line else ":"
        name, url = line.split(sep, 1)
        name, url = name.strip(), url.strip()
        repo = url.replace("https://github.com/", "").rstrip("/").removesuffix("/releases")
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(api, headers={"User-Agent": "docker-build"})
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
        for asset in data["assets"]:
            if asset["name"].endswith(".opk"):
                dest = os.path.join(PLUGIN_DIR, name)
                print(f"Downloading {name} ← {asset['browser_download_url']}")
                urllib.request.urlretrieve(asset["browser_download_url"], dest)
                manifest.append(
                    {
                        "name": name,
                        "repo": repo,
                        "version": data.get("tag_name") or data.get("name") or "",
                        "published_at": beijing_time(data.get("published_at")),
                        "asset": asset["name"],
                    }
                )
                break
        else:
            print(f"WARNING: No .opk asset found for {name}")

with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
    f.write("\n")
