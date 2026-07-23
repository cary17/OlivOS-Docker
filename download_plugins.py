#!/usr/bin/env python3
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_DIR = Path(os.environ.get('PLUGIN_DIR', 'OlivOS/plugin/app'))
MANIFEST_PATH = Path(os.environ.get('PLUGIN_MANIFEST', 'downloaded-plugins.json'))
BEIJING_TZ = timezone(timedelta(hours=8))
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUIRED_OPK_FILES = {'app.json', '__init__.py', 'main.py'}


def beijing_time(value):
    if not value:
        return ''
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return dt.astimezone(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S %z')


def parse_manifest_line(line):
    line = line.strip()
    if not line:
        return None
    separator = '：' if '：' in line else ':'
    if separator not in line:
        raise ValueError(f'Invalid OPK manifest entry: {line}')
    name, url = (part.strip() for part in line.split(separator, 1))
    prefix = 'https://github.com/'
    if not name.endswith('.opk') or not url.startswith(prefix):
        raise ValueError(f'Invalid OPK manifest entry: {line}')
    repo = url.removeprefix(prefix).rstrip('/').removesuffix('/releases')
    if len(repo.split('/')) != 2:
        raise ValueError(f'Invalid GitHub repository: {repo}')
    return name, repo


def request_json(url, token=''):
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'olivos-docker-build',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, headers=headers)
    last_error = None
    for attempt in range(REQUEST_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.load(response)
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < REQUEST_RETRIES:
                time.sleep(2**attempt)
    raise RuntimeError(f'Failed to request {url}') from last_error


def download_file(url, destination, retries=REQUEST_RETRIES, validator=None):
    destination = Path(destination)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    request = urllib.request.Request(url, headers={'User-Agent': 'olivos-docker-build'})
    last_error = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            with (
                urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response,
                temporary.open('wb') as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if validator is not None:
                validator(temporary)
            os.replace(temporary, destination)
            return
        except ValueError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f'Failed to download {url}') from last_error


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_opk(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = REQUIRED_OPK_FILES - names
            if missing:
                raise ValueError(f'OPK missing required files: {sorted(missing)}')
            if archive.testzip() is not None:
                raise ValueError('OPK contains a corrupt file')
            app = json.loads(archive.read('app.json'))
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError(f'Invalid OPK archive: {path}') from exc
    if not isinstance(app, dict) or not app.get('namespace'):
        raise ValueError('OPK app.json must contain a namespace')
    return app


def download_plugins(opk_path='opk.txt', token=''):
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    with Path(opk_path).open(encoding='utf-8') as file:
        for line_number, line in enumerate(file, 1):
            parsed = parse_manifest_line(line)
            if parsed is None:
                continue
            name, repo = parsed
            api = f'https://api.github.com/repos/{repo}/releases/latest'
            release = request_json(api, token)
            asset = next(
                (item for item in release.get('assets', []) if item.get('name', '').endswith('.opk')),
                None,
            )
            if asset is None:
                raise RuntimeError(f'No OPK asset found for {repo} (manifest line {line_number})')
            destination = PLUGIN_DIR / name
            print(f"Downloading {name} ← {asset['browser_download_url']}")
            app = None

            def validate_download(path):
                nonlocal app
                app = validate_opk(path)

            download_file(asset['browser_download_url'], destination, validator=validate_download)
            manifest.append(
                {
                    'name': name,
                    'repo': repo,
                    'version': release.get('tag_name') or release.get('name') or '',
                    'published_at': beijing_time(release.get('published_at')),
                    'asset': asset['name'],
                    'asset_id': asset.get('id'),
                    'sha256': sha256_file(destination),
                    'namespace': app['namespace'],
                }
            )
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return manifest


def main():
    download_plugins(token=os.environ.get('GITHUB_TOKEN', ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
