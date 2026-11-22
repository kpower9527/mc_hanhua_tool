from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..applied import pointer_path
from ..brand import PACK_DESCRIPTION
from ..mcversion import parse_version
from ..handlers import all_handlers
from ..handlers.base import OutputSink
from ..respacks import PatchIndex, installed_patches, pack_name, read_pack, resolve_pack
from ..store import Workspace
from .options import add_pack_top, get_option, place_around, read_packs, remove_packs, set_option
from .resourcepack import build_pack_zip, pack_mcmeta

MODE_STANDALONE = "standalone"
MODE_I18N = "i18nupdate"
MODES = (MODE_STANDALONE, MODE_I18N)

PACK_FILE = "maikuai-zh_cn.zip"
FILL_FILE = "maikuai-zh_cn-fill.zip"
FIX_FILE = "maikuai-zh_cn-fix.zip"
PACK_ID = f"file/{PACK_FILE}"
# 0.1.6 及以前的文件名：识别旧版应用的结果
LEGACY_TOP_FILES = ("mctrans-zh_cn.zip", "mctrans-zh_cn-fix.zip")

# 每种模式下各资源包收录哪些来源的译文
PACK_ORIGINS: dict[str, dict[str, frozenset[str]]] = {
    MODE_STANDALONE: {PACK_FILE: frozenset({"cfpa", "respack", "memory", "llm", "human"})},
    MODE_I18N: {FILL_FILE: frozenset({"memory", "llm"}), FIX_FILE: frozenset({"respack", "human"})},
}
INSTANCE_EXCLUDED = frozenset({"mod_builtin"})
# 整文件处理器的输出位置（配合模式下放在 CFPA 包之下，CFPA 有同名文件时以 CFPA 为准）
WHOLE_FILE_PACK = {MODE_STANDALONE: PACK_FILE, MODE_I18N: FILL_FILE}


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


def pack_id(mc_version: str, file_name: str) -> str:
    """options.txt 中的资源包 ID：1.13 起带 file/ 前缀。"""
    return f"file/{file_name}" if parse_version(mc_version) >= (1, 13, 0) else file_name


def default_packs(mc_version: str) -> list[str]:
    return ["vanilla"] if parse_version(mc_version) >= (1, 13, 0) else []


def i18n_pack_id(mc_version: str) -> str:
    return pack_id(mc_version, f"Minecraft-Mod-Language-Modpack-Converted-{mc_version}.zip")


def resolve_mode(mode: str, has_i18nupdatemod: bool) -> str:
    if mode == "auto":
        return MODE_I18N if has_i18nupdatemod else MODE_STANDALONE
    if mode not in MODES:
        raise ApplyError(f"未知的输出模式：{mode}（可选：auto、{'、'.join(MODES)}）")
    return mode


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_applied(root: Path) -> bool:
    return pointer_path(Path(root)).exists()


def build_outputs(ws: Workspace, mode: str, root: Path | None = None) -> Outputs:
    plan = PACK_ORIGINS[mode]
    packs = {name: OutputSink(root) for name in plan}
    instance = OutputSink(root)
    count = 0
    for handler in all_handlers():
        items = ws.translated_items(handler.name)
        if handler.output == "instance" or handler.whole_file:
            chosen = [it for it in items if it.origin not in INSTANCE_EXCLUDED]
            if chosen:
                target = instance if handler.output == "instance" else packs[WHOLE_FILE_PACK[mode]]
                handler.write(chosen, target)
                count += len(chosen)
            continue
        for name, origins in plan.items():
            chosen = [it for it in items if it.origin in origins]
            if chosen:
                handler.write(chosen, packs[name])
                count += len(chosen)
    return Outputs({n: s for n, s in packs.items() if s.pack_files}, instance, count)


def skip_patch_files(outputs: Outputs, root: Path) -> None:
    """已安装的汉化补丁提供的非语言文件（手册等整文件），不再写入我们的资源包，以补丁的人工翻译为准。
    语言文件按条目合并，补丁中的译文已在扫描时吸收，无需跳过。"""
    provided = PatchIndex(installed_patches(root)).non_lang_files()
    if not provided:
        return
    for name, sink in list(outputs.packs.items()):
        for rel in [r for r in sink.pack_files if r.lower() in provided]:
            del sink.pack_files[rel]
        if not sink.pack_files:
            del outputs.packs[name]


def top_pack(mc_version: str, packs: list[str]) -> str | None:
    """应当排在最上面的那个 mctrans 资源包（配合 I18nUpdateMod 时补全包本来就在 CFPA 包下面）。"""
    for name in (PACK_FILE, FIX_FILE, *LEGACY_TOP_FILES):
        pid = pack_id(mc_version, name)
        if pid in packs:
            return pid
    return None


def packs_above_ours(root: Path, mc_version: str) -> list[str]:
    """已应用的汉化资源包上方、会盖掉我们译文的资源包：含 zh_cn 语言文件，或与我们的包有同名文件。
    纹理包、其他语言的翻译包、模组自带的内置资源包不影响中文显示，不算在内。"""
    root = Path(root)
    if not is_applied(root):
        return []
    try:
        manifest = json.loads((root / json.loads(pointer_path(root).read_text(encoding="utf-8"))["manifest"])
                              .read_text(encoding="utf-8"))
        ours = top_pack(mc_version, manifest["options"]["packs"])
        text = (root / "options.txt").read_bytes().decode("utf-8", "replace")
    except (OSError, ValueError, KeyError):
        return []
    packs = read_packs(text) or []
    if ours is None or ours not in packs:
        return []
    own = read_pack(root / "resourcepacks" / pack_name(ours), [])
    own_files = own.files if own else set()
    result = []
    for pid in reversed(packs[packs.index(ours) + 1:]):
        path = resolve_pack(root, pid)
        if path is None:  # 模组提供的内置资源包等
            continue
        pack = read_pack(path, [])
        if pack is not None and (pack.lang or pack.files & own_files):
            result.append(pack_name(pid))
    return result


def apply(root: Path, ws: Workspace, mc_version: str, mode: str = MODE_STANDALONE,
          set_lang: bool = False, dry_run: bool = False) -> ApplyReport:
    root = Path(root)
    outputs = build_outputs(ws, mode, root)
    skip_patch_files(outputs, root)
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
            manifest["options"]["packs"].append(pack_id(mc_version, name))
        defaults = default_packs(mc_version)
        if mode == MODE_STANDALONE and PACK_FILE in outputs.packs:
            text = add_pack_top(text, pack_id(mc_version, PACK_FILE), defaults)
        elif mode == MODE_I18N and outputs.packs:
            below = [pack_id(mc_version, FILL_FILE)] if FILL_FILE in outputs.packs else []
            above = [pack_id(mc_version, FIX_FILE)] if FIX_FILE in outputs.packs else []
            text = place_around(text, i18n_pack_id(mc_version), below, above, defaults)
        if set_lang:
            manifest["options"]["lang_set"] = True
            manifest["options"]["lang_before"] = get_option(text, "lang")
            text = set_option(text, "lang", "zh_cn")
        opts.write_bytes(text.encode("utf-8"))

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pointer_path(root).write_text(
        json.dumps({"manifest": manifest_path.relative_to(root).as_posix()}), encoding="utf-8"
    )
    return report


def restore(root: Path) -> RestoreReport:
    root = Path(root)
    pointer = pointer_path(root)
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
