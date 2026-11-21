"""读取 apply 之前的原始文件，避免把自己写出的译文当作整合包自带内容。"""
from __future__ import annotations

import json
from pathlib import Path


def pointer_path(root: Path) -> Path:
    return Path(root) / ".mctrans" / "applied.json"


def _manifest(root: Path) -> dict | None:
    pointer = pointer_path(root)
    if not pointer.is_file():
        return None
    try:
        rel = json.loads(pointer.read_text(encoding="utf-8"))["manifest"]
        return json.loads((Path(root) / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None


def read_original(root: Path, rel: str) -> bytes | None:
    root = Path(root)
    manifest = _manifest(root)
    if manifest:
        for item in manifest.get("modified", []):
            if item.get("path") == rel:
                backup = item.get("backup")
                return (root / backup).read_bytes() if backup else None
    path = root / rel
    return path.read_bytes() if path.is_file() else None
