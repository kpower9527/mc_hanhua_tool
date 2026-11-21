"""图形界面中"最近使用的整合包"列表，保存在 ~/.mctrans/instances.json。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import home_dir


@dataclass
class InstanceRecord:
    path: str
    name: str
    mc_version: str = ""  # 自动识别失败时用户手动填写的版本


def _file() -> Path:
    return home_dir() / "instances.json"


def load_instances() -> list[InstanceRecord]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [InstanceRecord(str(d["path"]), str(d.get("name") or Path(d["path"]).name), str(d.get("mc_version") or ""))
            for d in data if isinstance(d, dict) and d.get("path")]


def _save(records: list[InstanceRecord]) -> list[InstanceRecord]:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def add_instance(path: Path, mc_version: str = "") -> list[InstanceRecord]:
    resolved = str(Path(path).resolve())
    old = {r.path: r for r in load_instances()}
    version = mc_version or (old[resolved].mc_version if resolved in old else "")
    records = [r for r in old.values() if r.path != resolved]
    return _save([InstanceRecord(resolved, Path(resolved).name, version)] + records)


def find_instance(path: Path | str) -> InstanceRecord | None:
    resolved = str(Path(path).resolve())
    return next((r for r in load_instances() if r.path == resolved), None)


def remove_instance(path: Path | str) -> list[InstanceRecord]:
    resolved = str(Path(path).resolve())
    return _save([r for r in load_instances() if r.path != resolved])
