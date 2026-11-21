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


def _file() -> Path:
    return home_dir() / "instances.json"


def load_instances() -> list[InstanceRecord]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [InstanceRecord(str(d["path"]), str(d.get("name") or Path(d["path"]).name))
            for d in data if isinstance(d, dict) and d.get("path")]


def _save(records: list[InstanceRecord]) -> list[InstanceRecord]:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def add_instance(path: Path) -> list[InstanceRecord]:
    resolved = str(Path(path).resolve())
    records = [r for r in load_instances() if r.path != resolved]
    return _save([InstanceRecord(resolved, Path(resolved).name)] + records)


def remove_instance(path: Path | str) -> list[InstanceRecord]:
    resolved = str(Path(path).resolve())
    return _save([r for r in load_instances() if r.path != resolved])
