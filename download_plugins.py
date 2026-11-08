#!/usr/bin/env python3
"""Download and validate plugin OPKs for the full image build.

资源边界：下载、归档展开、重包与最终占用都有固定字节预算，
单资产由独立 worker 在绝对时限内处理，失败不覆盖已有好文件、不发布新清单。
渠道化：按仓库聚合所需插件，分页获取 Release，stable/testing 按
API 的 prerelease 标志与版本比较规则选择完整资产集合。
"""
import hashlib
import json
import multiprocessing
import os
import re
import resource
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from queue import Empty

from scripts.build_metadata import (
    BEIJING_TZ,
    beijing_time,
    normalize_version,
    parse_time,
    request_json,
    version_key,
)

PLUGIN_DIR = Path(os.environ.get('PLUGIN_DIR', 'OlivOS/plugin/app'))
MANIFEST_PATH = Path(os.environ.get('PLUGIN_MANIFEST', 'downloaded-plugins.json'))
REQUIRED_OPK_FILES = {'app.json', '__init__.py', 'main.py'}
CHANNELS = ('stable', 'testing')
DEFAULT_CHANNEL = 'stable'

RELEASES_PAGE_SIZE = 100
MAX_RELEASE_PAGES = 10
USER_AGENT = 'olivos-docker-build'

DOWNLOAD_TIMEOUT = 30
DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

# 固定资源预算（建议初值；字节按 MiB/GiB 换算，不引入配置框架）。
MAX_DOWNLOAD_BYTES = 1024**3          # 单资产原始下载 1 GiB
MAX_MEMBER_BYTES = 256 * 1024**2      # 单成员实际展开 256 MiB
MAX_ARCHIVE_BYTES = 1024**3           # 单归档累计实际展开 1 GiB
MAX_APP_JSON_BYTES = 4 * 1024**2      # app.json 4 MiB
MAX_ARCHIVE_MEMBERS = 10000           # 归档成员数（目录计入）
MAX_REPACK_BYTES = 1024**3            # 重包输出 1 GiB
MAX_TOTAL_PLUGIN_BYTES = 2 * 1024**3  # 本次插件集合最终累计 2 GiB
ASSET_TIME_BUDGET = 300               # 单资产全流程 300 秒（不按重试重置）
WORKER_ADDRESS_SPACE = 512 * 1024**2  # 资产 worker 地址空间 512 MiB
MAX_COMPRESSION_RATIO = 100           # 整体声明膨胀比预筛（非可靠额度）
WORKER_GRACE_PERIOD = 5.0             # terminate → kill 的宽限秒数
QUEUE_POLL_INTERVAL = 0.1
RESULT_DRAIN_TIMEOUT = 1.0


class AssetError(RuntimeError):
    """资产级永久失败（超限、超时、损坏、解析或 worker 异常）；不做网络重试。"""


class _TransientDownloadError(Exception):
    """传输不一致（如长度声明与实际不符）；可重试，但绝不提交截断文件。"""


def _current_budgets():
    return {
        'download': MAX_DOWNLOAD_BYTES,
        'member': MAX_MEMBER_BYTES,
        'archive': MAX_ARCHIVE_BYTES,
        'members': MAX_ARCHIVE_MEMBERS,
        'app_json': MAX_APP_JSON_BYTES,
        'repack': MAX_REPACK_BYTES,
        'total': MAX_TOTAL_PLUGIN_BYTES,
        'time': ASSET_TIME_BUDGET,
        'address_space': WORKER_ADDRESS_SPACE,
        'ratio': MAX_COMPRESSION_RATIO,
    }


def parse_manifest_line(line):
    line = line.strip()
    if not line:
        return None
    separator = '：' if '：' in line else ':'
    if separator not in line:
        raise ValueError(f'Invalid OPK manifest entry: {line}')
    name, url = (part.strip() for part in line.split(separator, 1))
    if '\ufeff' in name:
        # 行中 BOM 不允许被静默清洗后继续参与资产匹配。
        raise ValueError(f'OPK manifest name contains U+FEFF: {name!r}')
    prefix = 'https://github.com/'
    if (
        not name.endswith('.opk')
        or name == '.opk'
        or '/' in name
        or '\\' in name
        or ':' in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or not url.startswith(prefix)
    ):
        raise ValueError(f'Invalid OPK manifest entry: {line}')
    repo = url.removeprefix(prefix).removesuffix('/').removesuffix('/releases')
    parts = repo.split('/')
    if len(parts) != 2 or any(
        part in ('', '.', '..') or re.fullmatch(r'[A-Za-z0-9_.-]+', part) is None
        for part in parts
    ):
        raise ValueError(f'Invalid GitHub repository: {repo}')
    return name, repo


# ==================== 受限下载 ====================


def _declared_length(headers):
    value = headers.get('Content-Length') if headers is not None else None
    if value is None:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def _download_bounded(url, destination, budgets, deadline, retries=DOWNLOAD_RETRIES):
    """Download within byte and time budgets; counts actual bytes per chunk.

    Content-Length 只做预筛；没有长度或声明过小时仍按实际数据计数。
    长度声明与实际不一致按传输错误处理（可重试）；超限是永久资产错误。
    """
    destination = Path(destination)
    last_error = None
    for attempt in range(retries):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssetError(f'download budget exhausted before attempt {attempt + 1} for {url}')
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=min(DOWNLOAD_TIMEOUT, remaining)) as response:
                declared = _declared_length(response.headers)
                if declared is not None and declared > budgets['download']:
                    raise AssetError(
                        f'download of {url} declares {declared} bytes, '
                        f'over the {budgets["download"]} byte limit'
                    )
                total = 0
                # read1 避免为凑满整块而等待，滴流响应能在超时内及时失败。
                read = getattr(response, 'read1', None) or response.read
                with destination.open('wb') as output:
                    while True:
                        chunk = read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > budgets['download']:
                            raise AssetError(
                                f'download of {url} exceeded the {budgets["download"]} byte limit'
                            )
                        output.write(chunk)
                if declared is not None and total != declared:
                    raise _TransientDownloadError(f'{total} of {declared} bytes received from {url}')
            return total
        except AssetError:
            destination.unlink(missing_ok=True)
            raise
        except _TransientDownloadError as exc:
            last_error = exc
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code <= 599:
                last_error = exc
            else:
                destination.unlink(missing_ok=True)
                raise AssetError(f'download of {url} failed: HTTP {exc.code}') from exc
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        destination.unlink(missing_ok=True)
        if attempt + 1 < retries:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # 退避在绝对 deadline 内截断；预算不按重试重置。
            time.sleep(max(0.0, min(2**attempt, remaining)))
    destination.unlink(missing_ok=True)
    raise AssetError(f'failed to download {url}: {last_error}')


def download_file(url, destination, retries=DOWNLOAD_RETRIES, validator=None, budgets=None):
    """Bounded download; runs validator and atomically commits, returning its metadata."""
    budgets = budgets if budgets is not None else _current_budgets()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    deadline = time.monotonic() + budgets['time']
    try:
        _download_bounded(url, temporary, budgets, deadline, retries=retries)
        metadata = validator(temporary) if validator is not None else None
        os.replace(temporary, destination)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


# ==================== 有界归档校验 ====================


def _check_expansion_ratio(archive, budgets, raw_size):
    """整体声明膨胀比预筛；分母使用实际文件长度，不依赖 zipfile 私有属性。"""
    if not raw_size:
        return
    declared = sum(info.file_size for info in archive.infolist())
    if declared > budgets['ratio'] * raw_size:
        raise AssetError(
            f'archive declares {declared} expanded bytes from a {raw_size} byte file, '
            f'over the {budgets["ratio"]}x ratio limit'
        )


def _read_member_bounded(archive, info, budgets, collect=False):
    """Read one member to EOF in chunks, validating CRC, size, and limits."""
    total = 0
    collected = bytearray()
    try:
        with archive.open(info) as member_file:
            while True:
                chunk = member_file.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > budgets['member']:
                    raise AssetError(
                        f'archive member expands beyond the {budgets["member"]} byte limit: {info.filename}'
                    )
                if collect:
                    if total > budgets['app_json']:
                        raise AssetError(f'app.json exceeds the {budgets["app_json"]} byte limit')
                    collected.extend(chunk)
    except zipfile.BadZipFile as exc:
        # 到 EOF 的分块读取会触发 zipfile 的 CRC 校验。
        raise ValueError(f'archive member is corrupt: {info.filename}') from exc
    if total != info.file_size:
        raise AssetError(
            f'archive member {info.filename} declares {info.file_size} bytes but expanded to {total}'
        )
    return total, bytes(collected) if collect else None


def _validate_archive_members(archive, budgets):
    """Single bounded traversal: metadata pre-screen, then one read pass.

    路径、重复成员、加密、符号链接/特殊文件、目录内容与声明额度先拒绝；
    实际读取校验单成员与全归档展开量，同一趟收集 app.json。
    返回 ([(info, name)] 文件成员, app.json 原始字节或 None)。
    """
    infos = archive.infolist()
    if len(infos) > budgets['members']:
        raise AssetError(f'archive contains more than {budgets["members"]} members')
    seen = set()
    declared_total = 0
    for info in infos:
        name = info.filename
        member = name[:-1] if info.is_dir() else name
        if (
            not member
            or '\\' in name
            or ':' in name
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or any(part in ('', '.', '..') for part in member.split('/'))
        ):
            raise ValueError(f'Invalid ZIP member path: {name}')
        if member in seen:
            raise ValueError(f'Duplicate ZIP member: {name}')
        seen.add(member)
        if info.flag_bits & 0x1:
            raise AssetError(f'archive member is encrypted: {name}')
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise AssetError(f'archive member is not a regular file: {name}')
        if info.is_dir():
            if info.file_size:
                raise AssetError(f'archive directory member declares non-zero content: {name}')
            continue
        if info.file_size > budgets['member']:
            raise AssetError(
                f'archive member declares {info.file_size} bytes, '
                f'over the {budgets["member"]} byte limit: {name}'
            )
        declared_total += info.file_size
        if declared_total > budgets['archive']:
            raise AssetError(f'archive declares more than {budgets["archive"]} expanded bytes')
    members = []
    app_bytes = None
    actual_total = 0
    for info in infos:
        if info.is_dir():
            continue
        is_app = info.filename == 'app.json'
        size, data = _read_member_bounded(archive, info, budgets, collect=is_app)
        actual_total += size
        if actual_total > budgets['archive']:
            raise AssetError(f'archive expands beyond the {budgets["archive"]} byte limit')
        if is_app:
            app_bytes = data
        members.append(info)
    return members, app_bytes


def _parse_app_json(app_bytes):
    if app_bytes is None:
        raise ValueError("OPK missing required files: ['app.json']")
    try:
        app = json.loads(app_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'invalid app.json: {exc}') from exc
    namespace = app.get('namespace') if isinstance(app, dict) else None
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError('OPK app.json must contain a non-empty string namespace')
    return app


def validate_opk(path, budgets=None, raw_size=None):
    """Validate a standard OPK in place; original bytes are never rewritten."""
    budgets = budgets if budgets is not None else _current_budgets()
    try:
        with zipfile.ZipFile(path) as archive:
            _check_expansion_ratio(archive, budgets, raw_size)
            members, app_bytes = _validate_archive_members(archive, budgets)
            missing = REQUIRED_OPK_FILES - {info.filename for info in members}
            if missing:
                raise ValueError(f'OPK missing required files: {sorted(missing)}')
            app = _parse_app_json(app_bytes)
    except zipfile.BadZipFile as exc:
        raise ValueError(f'Invalid OPK archive: {path}') from exc
    return app


def normalize_release_zip(path, budgets=None, raw_size=None):
    """Validate a release ZIP and repack it to a plain OPK, within output limits.

    保留根目录布局与唯一 root-wrapper 两种布局及嵌套资源；重包使用字段
    受控的新 ZipInfo，由库重新计算大小与 CRC，不盲拷源 extra/flags/尺寸。
    """
    budgets = budgets if budgets is not None else _current_budgets()
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.normalized')
    try:
        with zipfile.ZipFile(path) as archive:
            _check_expansion_ratio(archive, budgets, raw_size)
            members, _ = _validate_archive_members(archive, budgets)
            names = {info.filename for info in members}
            if REQUIRED_OPK_FILES.issubset(names):
                prefix = ''
            else:
                roots = {name.split('/', 1)[0] for name in names if '/' in name}
                if len(roots) != 1:
                    raise ValueError('Release ZIP must contain exactly one plugin directory')
                prefix = roots.pop() + '/'
                if any(not name.startswith(prefix) for name in names):
                    raise ValueError('Release ZIP contains files outside its plugin directory')
            stripped = [info.filename.removeprefix(prefix) for info in members]
            missing = REQUIRED_OPK_FILES - set(stripped)
            if missing:
                raise ValueError(f'Release ZIP missing required files: {sorted(missing)}')
            if len(set(stripped)) != len(stripped):
                raise ValueError('Release ZIP members collide after stripping the plugin directory')
            written = 0
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as output:
                for (info, target_name) in zip(members, stripped):
                    zinfo = zipfile.ZipInfo(target_name, date_time=(1980, 1, 1, 0, 0, 0))
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    zinfo.external_attr = 0o644 << 16
                    with archive.open(info) as source, output.open(zinfo, 'w') as target:
                        while chunk := source.read(DOWNLOAD_CHUNK_SIZE):
                            written += len(chunk)
                            if written > budgets['repack']:
                                raise AssetError(
                                    f'repacked archive exceeds the {budgets["repack"]} byte output limit'
                                )
                            target.write(chunk)
        os.replace(temporary, path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f'Invalid Release ZIP archive: {path}') from exc
    finally:
        temporary.unlink(missing_ok=True)
    return validate_opk(path, budgets)


# ==================== 渠道化选择 ====================


def fetch_repo_releases(repo, token):
    """Paginate a repository's releases to a short page; budget exhaustion fails."""
    releases = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        url = f'https://api.github.com/repos/{repo}/releases?per_page={RELEASES_PAGE_SIZE}&page={page}'
        batch = request_json(url, token)
        if not isinstance(batch, list):
            raise RuntimeError(f'Unexpected release payload from {url}: expected a JSON list')
        releases.extend(batch)
        if len(batch) < RELEASES_PAGE_SIZE:
            return releases
    raise RuntimeError(f'Release pagination budget ({MAX_RELEASE_PAGES} pages) exhausted for {repo}')


def _complete_releases(releases, required_names):
    """Releases where every required name maps to a distinct asset (one-to-one)."""
    complete = []
    for release in releases:
        if not isinstance(release, dict) or release.get('draft'):
            continue
        mapping = {}
        claimed = set()
        for name in required_names:
            asset = select_release_asset(release, name)
            if asset is None:
                break
            key = asset.get('id', asset.get('name'))
            if key in claimed:
                break
            claimed.add(key)
            mapping[name] = asset
        if len(mapping) == len(required_names):
            complete.append((release, mapping))
    return complete


def _plugin_version_key(tag):
    """插件侧扩展版本比较键：在核心 version_key 语义上适配真实插件 tag 写法。

    - 纯数字主段（可带 prerelease 后缀）：原样使用核心语义，层级 (1, ...)
    - 多段数字主段 + 尾部括号构建号（3.0.2(10)）：主段语义 + 构建号次序
    - 单词主段（base-ea1）：视为编号前的"基础系列"，层级 (0, ...)，排于一切
      数字主段之前；系列内按后缀解析（ea1 < ea2）
    - 其余（如疑似日期式的单段数字加构建号 v20260920(16)）：返回 None，
      由调用方按发布时间兜底并告警；不同日历格式不强行折算成版本号
    """
    tag = normalize_version(tag or '')
    build = None
    bracket = re.fullmatch(r'(?P<body>.+)\((?P<build>\d+)\)', tag)
    if bracket:
        tag, build = bracket.group('body'), int(bracket.group('build'))
    main, separator, suffix = tag.partition('-')
    if main and all(part.isdigit() for part in main.split('.')):
        if build is not None and '.' not in main:
            return None
        key = (1,) + version_key(tag)
        return key + ((build,) if build is not None else ())
    if main:
        series = main.lower()
        suffix_key = version_key('0-' + suffix)[1] if separator else (1,)
        key = (0, series, suffix_key)
        return key + ((build,) if build is not None else ())
    return None


def _uses_extended_tag_form(tag):
    """非标准但可比较的写法（单词主段或括号构建号）；排序假设需要告警。"""
    tag = normalize_version(tag or '')
    if re.fullmatch(r'.+\(\d+\)', tag):
        return True
    main = tag.partition('-')[0]
    return bool(main) and not all(part.isdigit() for part in main.split('.'))


def _pick_category_latest(candidates, repo, label):
    """Pick the newest release within one S/P category.

    全部候选可用插件侧比较键排序时按 (key, 有效发布时间, release id) 取最新；
    存在不可比较 tag 的类别整体按有效发布时间兜底并告警，时间缺失则失败。
    单个候选无需比较，直接选用。
    """
    if len(candidates) == 1:
        return candidates[0]
    keys = [_plugin_version_key(release.get('tag_name') or '') for release, _ in candidates]
    for release, _ in candidates:
        if _uses_extended_tag_form(release.get('tag_name') or ''):
            print(f"WARNING: {repo} {label} release tag {release.get('tag_name')!r} "
                  "uses a non-standard version form; ordered by the extended plugin comparator")
    if any(key is None for key in keys):
        times = [parse_time(release.get('published_at')) for release, _ in candidates]
        if any(moment is None for moment in times):
            raise RuntimeError(
                f'Cannot select the newest {label} release for {repo}: '
                'tags are incomparable and publish times are missing'
            )
        print(f'WARNING: {repo} {label} releases have incomparable tags; selected by publish time')
        return max(
            zip(candidates, times),
            key=lambda item: (item[1], item[0][0].get('id') or 0),
        )[0]

    def sort_key(item):
        release, _ = item
        moment = parse_time(release.get('published_at'))
        return (
            _plugin_version_key(release.get('tag_name') or ''),
            moment or datetime.min.replace(tzinfo=BEIJING_TZ),
            release.get('id') or 0,
        )

    return max(candidates, key=sort_key)


def select_channel_release(releases, required_names, channel, repo):
    """选择提供全部所需资产且一一对应的发布；决策表仅以 API prerelease 标志为准。"""
    complete = _complete_releases(releases, required_names)
    stable = [item for item in complete if not bool(item[0].get('prerelease'))]
    prerelease = [item for item in complete if bool(item[0].get('prerelease'))]
    if channel == 'stable':
        if not stable:
            raise RuntimeError(
                f'No complete stable release for {repo} providing {sorted(required_names)} '
                '(channel stable; failing instead of skipping plugins or installing prereleases)'
            )
        return _pick_category_latest(stable, repo, 'stable')
    if not stable and not prerelease:
        raise RuntimeError(
            f'No complete release for {repo} providing {sorted(required_names)} (channel testing)'
        )
    if not prerelease:
        return _pick_category_latest(stable, repo, 'stable')
    if not stable:
        return _pick_category_latest(prerelease, repo, 'prerelease')
    stable_pick = _pick_category_latest(stable, repo, 'stable')
    prerelease_pick = _pick_category_latest(prerelease, repo, 'prerelease')
    stable_tag = stable_pick[0].get('tag_name') or ''
    prerelease_tag = prerelease_pick[0].get('tag_name') or ''
    stable_key = _plugin_version_key(stable_tag)
    prerelease_key = _plugin_version_key(prerelease_tag)
    if stable_key is None or prerelease_key is None:
        # 仅当扩展比较器也无法解析（如不同日历格式的日期式 tag）时，
        # 才按发布时间兜底，并打印明确告警——不允许无告警的静默降级。
        stable_time = parse_time(stable_pick[0].get('published_at'))
        prerelease_time = parse_time(prerelease_pick[0].get('published_at'))
        if stable_time is None or prerelease_time is None:
            raise RuntimeError(
                f'Cannot compare plugin release tags across channels for {repo}: '
                f'{prerelease_tag!r} (prerelease) vs {stable_tag!r} (stable) and '
                'publish times are missing; fix the release metadata'
            )
        print(f'WARNING: {repo} prerelease tag {prerelease_tag!r} is not comparable with '
              f'stable tag {stable_tag!r}; selected by publish time')
        if (prerelease_time, prerelease_pick[0].get('id') or 0) >= (stable_time, stable_pick[0].get('id') or 0):
            return prerelease_pick
        return stable_pick
    if prerelease_key >= stable_key:
        return prerelease_pick
    return stable_pick


def select_release_asset(release, name):
    assets = release.get('assets', [])
    for asset in assets:
        if asset.get('name') == name:
            return asset
    if any(asset.get('name', '').endswith('.opk') for asset in assets):
        return None
    archives = [asset for asset in assets if asset.get('name', '').endswith('.zip')]
    return next((asset for asset in archives if asset['name'] == Path(name).with_suffix('.zip').name),
                archives[0] if len(archives) == 1 else None)


# ==================== 资产 worker 与 watchdog ====================


def _apply_worker_limits(budgets):
    limit = int(budgets.get('address_space') or 0)
    if limit > 0:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _asset_worker(job, queue):
    """Run download/validation/repack for one asset; report a bounded result.

    worker 不接触正式目标文件，也不做任何替换；它把产物写进 staging。
    """
    try:
        budgets = job['budgets']
        _apply_worker_limits(budgets)
        payload = Path(job['staging_dir']) / 'payload.bin'
        deadline = time.monotonic() + budgets['time']
        if job.get('url'):
            _download_bounded(job['url'], payload, budgets, deadline)
        else:
            source = Path(job['source'])
            if source.stat().st_size > budgets['download']:
                raise AssetError(
                    f'local plugin exceeds the {budgets["download"]} byte limit: {source.name}'
                )
            shutil.copyfile(source, payload)
        if job['kind'] == 'zip':
            app = normalize_release_zip(payload, budgets, raw_size=payload.stat().st_size)
        else:
            app = validate_opk(payload, budgets, raw_size=payload.stat().st_size)
        result = {
            'ok': True,
            'namespace': app['namespace'],
            'version': str(app.get('version', '') or '')[:512],
        }
    except AssetError as exc:
        result = {'ok': False, 'category': 'asset', 'message': str(exc)[:512]}
    except ValueError as exc:
        result = {'ok': False, 'category': 'invalid', 'message': f'{type(exc).__name__}: {exc}'[:512]}
    except MemoryError:
        result = {'ok': False, 'category': 'resource', 'message': 'worker ran out of address space'}
    except Exception as exc:  # 结果必须回传父进程归类，不允许静默消失
        result = {'ok': False, 'category': 'internal', 'message': f'{type(exc).__name__}: {exc}'[:512]}
    try:
        queue.put(result)
    except Exception:
        pass


def _spawn_worker(target, job):
    """Run one worker under an absolute deadline; terminate, then kill."""
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    process = context.Process(target=target, args=(job, queue), daemon=True)
    process.start()
    deadline = time.monotonic() + job['budgets']['time']
    result = None
    timed_out = False
    while True:
        try:
            result = queue.get(timeout=QUEUE_POLL_INTERVAL)
            break
        except Empty:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if not process.is_alive():
                try:
                    result = queue.get(timeout=RESULT_DRAIN_TIMEOUT)
                except Empty:
                    result = None
                break
    if timed_out:
        process.terminate()
        process.join(WORKER_GRACE_PERIOD)
        if process.is_alive():
            process.kill()
            process.join()
        raise AssetError(
            f'asset worker exceeded the {job["budgets"]["time"]}s budget and was terminated'
        )
    process.join()
    return result, process


def _run_asset_job(job, target=None):
    result, process = _spawn_worker(target or _asset_worker, job)
    if isinstance(result, dict) and result.get('ok'):
        return result
    if isinstance(result, dict):
        raise AssetError(f'{result.get("category", "error")}: {result.get("message", "unknown error")}')
    raise AssetError(f'asset worker exited without a result (exit code {process.exitcode})')


# ==================== 主流程 ====================


def _stage_payload(job, name, budgets, committed):
    """Run one asset job, enforce the cumulative budget, and commit atomically.

    staging 目录建在目标文件系统内；只有完整验证通过才由父进程 os.replace()
    提交。所有异常都清理本次 staging，不触碰其他文件。
    """
    staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=PLUGIN_DIR))
    try:
        result = _run_asset_job(dict(job, staging_dir=str(staging)))
        payload = staging / 'payload.bin'
        if not payload.is_file():
            raise AssetError(f'asset worker produced no payload for {name}')
        size = payload.stat().st_size
        projected = sum(committed.values()) - committed.get(name, 0) + size
        if projected > budgets['total']:
            raise AssetError(
                f'plugin payload for {name} would exceed the total budget of {budgets["total"]} bytes'
            )
        os.replace(payload, PLUGIN_DIR / name)
        committed[name] = size
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def download_plugins(opk_path='opk.txt', token='', local_dir='opk_local', channel=DEFAULT_CHANNEL):
    if channel not in CHANNELS:
        # 未知渠道在访问网络与写入输出目录之前失败。
        raise ValueError(f'Unknown plugin channel: {channel}')
    entries = []
    names = set()
    repos = {}
    # 只剥除文件头 BOM；行中 BOM 由 parse_manifest_line 拒绝。
    with Path(opk_path).open(encoding='utf-8-sig') as file:
        for line_number, line in enumerate(file, 1):
            parsed = parse_manifest_line(line)
            if parsed is None:
                continue
            name, repo = parsed
            if name in names:
                raise ValueError(f'Duplicate OPK manifest name: {name} (manifest line {line_number})')
            names.add(name)
            entries.append((line_number, name, repo))
            repo_names = repos.setdefault(repo, [])
            if name not in repo_names:
                repo_names.append(name)
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    selections = {}
    for repo, repo_names in repos.items():
        releases = fetch_repo_releases(repo, token)
        selections[repo] = select_channel_release(releases, repo_names, channel, repo)
    budgets = _current_budgets()
    manifest = []
    committed = {}
    for line_number, name, repo in entries:
        release, mapping = selections[repo]
        asset = mapping[name]
        asset_name = asset['name']
        print(f"Downloading {name} ← {asset['browser_download_url']}")
        result = _stage_payload(
            {
                'url': asset['browser_download_url'],
                'kind': 'zip' if asset_name.endswith('.zip') else 'opk',
                'budgets': budgets,
            },
            name,
            budgets,
            committed,
        )
        manifest.append(
            {
                'name': name,
                'repo': repo,
                'version': release.get('tag_name') or release.get('name') or '',
                'prerelease': bool(release.get('prerelease')),
                'published_at': beijing_time(release.get('published_at')),
                'asset': asset_name,
                'asset_id': asset.get('id'),
                'sha256': sha256_file(PLUGIN_DIR / name),
                'namespace': result['namespace'],
            }
        )
    for source in sorted(Path(local_dir).rglob('*.opk')):
        result = _stage_payload(
            {
                'source': str(source),
                'kind': 'opk',
                'budgets': budgets,
            },
            source.name,
            budgets,
            committed,
        )
        manifest = [item for item in manifest if item['name'] != source.name]
        manifest.append({
            'name': source.name,
            'source': 'local',
            'version': result.get('version', ''),
            'prerelease': False,
            'namespace': result['namespace'],
            'sha256': sha256_file(PLUGIN_DIR / source.name),
        })
    temporary = MANIFEST_PATH.with_suffix(MANIFEST_PATH.suffix + '.tmp')
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    os.replace(temporary, MANIFEST_PATH)
    return manifest


def main():
    download_plugins(
        token=os.environ.get('GITHUB_TOKEN', ''),
        channel=os.environ.get('PLUGIN_CHANNEL', DEFAULT_CHANNEL),
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
