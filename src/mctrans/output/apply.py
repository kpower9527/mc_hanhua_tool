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
from .options import add_pack_top, get_option, place_around, remove_packs, set_option
from .resourcepack import build_pack_zip, pack_mcmeta

MODE_STANDALONE = "standalone"
MODE_I18N = "i18nupdate"
MODES = (MODE_STANDALONE, MODE_I18N)

PACK_FILE = "mctrans-zh_cn.zip"
FILL_FILE = "mctrans-zh_cn-fill.zip"
FIX_FILE = "mctrans-zh_cn-fix.zip"
PACK_ID = f"file/{PACK_FILE}"
PACK_DESCRIPTION = "mctrans 自动汉化"

# 每种模式下各资源包收录哪些来源的译文
PACK_ORIGINS: dict[str, dict[str, frozenset[str]]] = {
    MODE_STANDALONE: {PACK_FILE: frozenset({"cfpa", "memory", "llm", "human"})},
    MODE_I18N: {FILL_FILE: frozenset({"memory", "llm"}), FIX_FILE: frozenset({"human"})},
}
INSTANCE_EXCLUDED = frozenset({"mod_builtin"})


class ApplyError(Exception):
    pass


@dataclass
class ApplyReport:
    entries: int
    mode: str
    packs: dict[str, int]
    instance_files: list[str]
    dry_run: bool


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class Outputs:
    packs: dict[str, OutputSink]
    instance: OutputSink
    entries: int


def i18n_pack_id(mc_version: str) -> str:
    return f"file/Minecraft-Mod-Language-Modpack-Converted-{mc_version}.zip"


def resolve_mode(mode: str, has_i18nupdatemod: bool) -> str:
    if mode == "auto":
        return MODE_I18N if has_i18nupdatemod else MODE_STANDALONE
    if mode not in MODES:
        raise ApplyError(f"未知的输出模式：{mode}（可选：auto、{'、'.join(MODES)}）")
    return mode


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pointer(root: Path) -> Path:
    return root / ".mctrans" / "applied.json"


def is_applied(root: Path) -> bool:
    return _pointer(Path(root)).exists()


def build_outputs(ws: Workspace, mode: str) -> Outputs:
    plan = PACK_ORIGINS[mode]
    packs = {name: OutputSink() for name in plan}
    instance = OutputSink()
    count = 0
    for handler in all_handlers():
        items = ws.translated_items(handler.name)
        if handler.output == "instance":
            chosen = [it for it in items if it.origin not in INSTANCE_EXCLUDED]
            if chosen:
                handler.write(chosen, instance)
                count += len(chosen)
            continue
        for name, origins in plan.items():
            chosen = [it for it in items if it.origin in origins]
            if chosen:
                handler.write(chosen, packs[name])
                count += len(chosen)
    return Outputs({n: s for n, s in packs.items() if s.pack_files}, instance, count)


def apply(root: Path, ws: Workspace, mc_version: str, mode: str = MODE_STANDALONE,
          set_lang: bool = False, dry_run: bool = False) -> ApplyReport:
    root = Path(root)
    outputs = build_outputs(ws, mode)
    report = ApplyReport(
        entries=outputs.entries, mode=mode,
        packs={name: len(sink.pack_files) for name, sink in outputs.packs.items()},
        instance_files=sorted(outputs.instance.instance_files), dry_run=dry_run,
    )
    if dry_run:
        return report
    if is_applied(root):
        restore(root)

    run_dir = root / ".mctrans" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True)
    manifest: dict = {"created": [], "modified": [], "options": {"packs": [], "lang_set": False, "lang_before": None}}

    for rel, data in sorted(outputs.instance.instance_files.items()):
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

    if outputs.packs or set_lang:
        opts = root / "options.txt"
        text = opts.read_bytes().decode("utf-8") if opts.exists() else ""
        for name, sink in sorted(outputs.packs.items()):
            data = build_pack_zip(sink.pack_files, pack_mcmeta(mc_version, PACK_DESCRIPTION))
            path = root / "resourcepacks" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest["created"].append({"path": f"resourcepacks/{name}", "hash": _sha256(data)})
            manifest["options"]["packs"].append(f"file/{name}")
        if mode == MODE_STANDALONE and PACK_FILE in outputs.packs:
            text = add_pack_top(text, PACK_ID)
        elif mode == MODE_I18N and outputs.packs:
            below = [f"file/{FILL_FILE}"] if FILL_FILE in outputs.packs else []
            above = [f"file/{FIX_FILE}"] if FIX_FILE in outputs.packs else []
            text = place_around(text, i18n_pack_id(mc_version), below, above)
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
