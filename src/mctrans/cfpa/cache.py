from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .versions import CfpaAsset

DEFAULT_BASE_URL = "https://github.com/CFPAOrg/Minecraft-Mod-Language-Package/releases/download/autobuild"


def http_fetch(url: str) -> bytes:
    import httpx

    with httpx.Client(follow_redirects=True, timeout=120.0, trust_env=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


@dataclass
class CacheResult:
    data: bytes | None
    status: str
    message: str = ""


class CfpaCache:
    def __init__(self, root: Path, base_url: str = DEFAULT_BASE_URL,
                 fetch: Callable[[str], bytes] = http_fetch, check_hours: float = 6.0,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.fetch = fetch
        self.check_seconds = check_hours * 3600
        self.clock = clock

    def _state_path(self, asset: CfpaAsset) -> Path:
        return self.root / f"{asset.key}.state.json"

    def _load_state(self, asset: CfpaAsset) -> dict:
        try:
            return json.loads(self._state_path(asset).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, asset: CfpaAsset, md5: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_path(asset).write_text(
            json.dumps({"md5": md5, "checked_at": self.clock()}), encoding="utf-8")

    def get(self, asset: CfpaAsset, offline: bool = False) -> CacheResult:
        zip_path = self.root / asset.zip_name
        cached = zip_path.read_bytes() if zip_path.is_file() else None
        if offline:
            return CacheResult(cached, "cached" if cached is not None else "missing")
        state = self._load_state(asset)
        if cached is not None and self.clock() - state.get("checked_at", 0) < self.check_seconds:
            return CacheResult(cached, "cached")
        try:
            remote_md5 = self.fetch(f"{self.base_url}/{asset.md5_name}").decode("ascii").strip().lower()
            if cached is not None and state.get("md5") == remote_md5:
                self._save_state(asset, remote_md5)
                return CacheResult(cached, "fresh")
            data = self.fetch(f"{self.base_url}/{asset.zip_name}")
            if hashlib.md5(data).hexdigest() != remote_md5:
                raise ValueError(f"{asset.zip_name} 校验失败，下载内容可能不完整")
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = zip_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(zip_path)
            self._save_state(asset, remote_md5)
            return CacheResult(data, "updated")
        except Exception as exc:  # 网络或校验错误：尽量用缓存继续
            if cached is not None:
                return CacheResult(cached, "cached", f"检查更新失败，使用本地缓存：{exc}")
            return CacheResult(None, "missing", f"下载失败：{exc}")
