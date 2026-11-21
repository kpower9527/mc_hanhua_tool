from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..handlers import all_handlers
from ..handlers.base import OutputSink
from ..store import Workspace
from .options import add_pack_top, get_option, remove_packs, set_option
from .resourcepack import build_pack_zip, pack_mcmeta

PACK_FILE = "mctrans-zh_cn.zip"
PACK_ID = f"file/{PACK_FILE}"
PACK_DESCRIPTION = "mctrans 自动汉化"
EXCLUDED_ORIGINS = {"mod_builtin"}  # jar 内已自带，无需重复写入


class ApplyError(Exception):
    pass


@dataclass
class ApplyReport:
    entries: int
    pack_files: int
    instance_files: list[str]
    pack_path: Path | None
    dry_run: bool


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pointer(root: Path) -> Path:
    return root / ".mctrans" / "applied.json"


def is_applied(root: Path) -> bool:
    return _pointer(Path(root)).exists()


def build_outputs(ws: Workspace) -> tuple[OutputSink, int]:
    sink = OutputSink()
    count = 0
    for handler in all_handlers():
        items = [it for it in ws.translated_items(handler.name) if it.origin not in EXCLUDED_ORIGINS]
        if items:
            handler.write(items, sink)
            count += len(items)
    return sink, count


def apply(root: Path, ws: Workspace, mc_version: str, set_lang: bool = False,
          dry_run: bool = False) -> ApplyReport:
    root = Path(root)
    sink, count = build_outputs(ws)
    pack_path = root / "resourcepacks" / PACK_FILE if sink.pack_files else None
    report = ApplyReport(count, len(sink.pack_files), sorted(sink.instance_files), pack_path, dry_run)
    if dry_run:
        return report
    if is_applied(root):
        restore(root)

    run_dir = root / ".mctrans" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True)
    manifest: dict = {"created": [], "modified": [], "options": {"packs": [], "lang_set": False, "lang_before": None}}

    for rel, data in sorted(sink.instance_files.items()):
        target = root / rel
        backup_rel = None
        if target.exists():
            backup = run_dir / "files" / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backup_rel = backup.relative_to(root).as_posix()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest["modified"].append({"path": rel, "backup": backup_rel, "hash": _sha256(data)})

    if pack_path is not None or set_lang:
        opts = root / "options.txt"
        text = opts.read_bytes().decode("utf-8") if opts.exists() else ""
        if pack_path is not None:
            data = build_pack_zip(sink.pack_files, pack_mcmeta(mc_version, PACK_DESCRIPTION))
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            pack_path.write_bytes(data)
            manifest["created"].append({"path": f"resourcepacks/{PACK_FILE}", "hash": _sha256(data)})
            text = add_pack_top(text, PACK_ID)
            manifest["options"]["packs"].append(PACK_ID)
        if set_lang:
            manifest["options"]["lang_set"] = True
            manifest["options"]["lang_before"] = get_option(text, "lang")
            text = set_option(text, "lang", "zh_cn")
        opts.write_bytes(text.encode("utf-8"))

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _pointer(root).write_text(
        json.dumps({"manifest": manifest_path.relative_to(root).as_posix()}), encoding="utf-8"
    )
    return report


def restore(root: Path) -> RestoreReport:
    root = Path(root)
    pointer = _pointer(root)
    if not pointer.exists():
        raise ApplyError("没有找到已应用的汉化记录，无需还原")
    manifest = json.loads((root / json.loads(pointer.read_text(encoding="utf-8"))["manifest"]).read_text(encoding="utf-8"))
    report = RestoreReport()

    for item in manifest["modified"]:
        target = root / item["path"]
        if target.exists() and _sha256(target.read_bytes()) != item["hash"]:
            report.skipped.append(item["path"])
            continue
        if item["backup"]:
            shutil.copy2(root / item["backup"], target)
            report.restored.append(item["path"])
        else:
            target.unlink(missing_ok=True)
            report.removed.append(item["path"])

    for item in manifest["created"]:
        target = root / item["path"]
        if target.exists() and _sha256(target.read_bytes()) != item["hash"]:
            report.skipped.append(item["path"])
            continue
        target.unlink(missing_ok=True)
        report.removed.append(item["path"])

    opts_info = manifest["options"]
    opts = root / "options.txt"
    if opts.exists() and (opts_info["packs"] or opts_info["lang_set"]):
        text = opts.read_bytes().decode("utf-8")
        text = remove_packs(text, opts_info["packs"])
        if opts_info["lang_set"]:
            text = set_option(text, "lang", opts_info["lang_before"] or "en_us")
        opts.write_bytes(text.encode("utf-8"))

    pointer.unlink()
    return report
