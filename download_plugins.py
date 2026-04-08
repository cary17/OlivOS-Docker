#!/usr/bin/env python3
import json
import os
import urllib.request

PLUGIN_DIR = "OlivOS/plugin/app"
os.makedirs(PLUGIN_DIR, exist_ok=True)

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
                print(f"Downloading {name} from {asset['browser_download_url']}")
                urllib.request.urlretrieve(asset["browser_download_url"], dest)
                break
        else:
            print(f"WARNING: No .opk asset found for {name}")
