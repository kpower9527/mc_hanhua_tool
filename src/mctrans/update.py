"""检查更新：查询 GitHub 上最新的正式版 Release，与当前版本比较。只提醒，不自动下载。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = "kpower9527/mc_hanhua_tool"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"  # 只返回正式版（不含草稿、预发布）
RELEASES_URL = f"https://github.com/{REPO}/releases"
TIMEOUT = 10.0
MAX_NOTES = 1500
STATE_FILE = "update.json"


class UpdateError(Exception):
    pass


@dataclass
class ReleaseInfo:
    version: str
    url: str
    notes: str
    newer: bool


def _parts(version: str) -> tuple[int, ...]:
    nums = [int(n) for n in re.findall(r"\d+", version)]
    while nums and nums[-1] == 0:  # 0.2 与 0.2.0 视为相同
        nums.pop()
    return tuple(nums)


def is_newer(candidate: str, current: str) -> bool:
    return _parts(candidate) > _parts(current)


def http_get(url: str) -> bytes:
    import httpx

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "maikuai-hanhua-update-check"}
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT, trust_env=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def check(current: str, fetch: Callable[[str], bytes] = http_get) -> ReleaseInfo:
    try:
        data = json.loads(fetch(LATEST_API))
    except Exception as exc:  # 网络错误、HTTP 错误、无法解析
        raise UpdateError(f"无法获取最新版本信息：{exc}") from exc
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not _parts(tag):
        raise UpdateError("GitHub 返回的版本信息不完整")
    version = tag.lstrip("vV")
    notes = (data.get("body") or "").strip()
    if len(notes) > MAX_NOTES:
        notes = notes[:MAX_NOTES] + "…"
    return ReleaseInfo(version, data.get("html_url") or RELEASES_URL, notes, is_newer(version, current))


def _state_path(home: Path) -> Path:
    return Path(home) / STATE_FILE


def skipped_version(home: Path) -> str | None:
    try:
        value = json.loads(_state_path(home).read_text(encoding="utf-8")).get("skipped")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def skip_version(home: Path, version: str) -> None:
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skipped": version}, ensure_ascii=False), encoding="utf-8")
