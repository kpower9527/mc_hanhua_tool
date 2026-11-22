"""整合包里已安装的第三方汉化补丁（resourcepacks 下的资源包）。

Minecraft 按资源包顺序叠加：语言文件按条目合并、排在上面的优先；其他文件整文件覆盖。
扫描时读取补丁中的 zh_cn 语言文件作为人工译文来源（优先于 CFPA 和机翻），
这样把 mctrans 汉化包放在最上面也不会盖掉补丁里的人工译文。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .handlers.lang_json import parse_any
from .output.options import read_packs

ZH_LANG_RE = re.compile(r"^assets/([^/]+)/lang/zh_cn\.(json|lang)$", re.IGNORECASE)
LANG_RE = re.compile(r"^assets/[^/]+/lang/[^/]+$", re.IGNORECASE)
OWN_PREFIX = "mctrans-"
# I18nUpdateMod 下载的 CFPA 包：已作为 CFPA 来源处理
SKIP_PREFIXES = ("Minecraft-Mod-Language-",)
# Paxi 模组强制加载的资源包（总是启用，资源包列表里的 ID 不带 file/ 前缀）
PAXI_DIR = "config/paxi/resourcepacks"


@dataclass
class Patch:
    name: str
    lang: dict[str, dict[str, str]] = field(default_factory=dict)   # 命名空间 → 键 → 译文
    files: set[str] = field(default_factory=set)                    # 包内 assets 下的文件（小写）


def pack_name(pack_id: str) -> str:
    return pack_id.removeprefix("file/")


def _is_pack(p: Path) -> bool:
    if p.name.startswith(OWN_PREFIX) or p.name.startswith(SKIP_PREFIXES):
        return False
    return (p.is_file() and p.suffix.lower() == ".zip") or (p.is_dir() and (p / "pack.mcmeta").is_file())


def _list(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()) if _is_pack(p)]


def resolve_pack(root: Path, pack_id: str) -> Path | None:
    """资源包 ID → 文件路径：file/x 在 resourcepacks 下；不带前缀的是 Paxi 包，或 1.12 的 resourcepacks 下的包。"""
    if pack_id.startswith("file/"):
        candidates = [root / "resourcepacks" / pack_name(pack_id)]
    else:
        candidates = [root / PAXI_DIR / pack_id, root / "resourcepacks" / pack_id]
    return next((p for p in candidates if p.exists() and _is_pack(p)), None)


def enabled_packs(root: Path, options_text: str | None = None) -> list[Path]:
    """游戏会加载的第三方资源包，按优先级从高到低（资源包列表中上面的在前）。
    没有 options.txt（游戏还没启动过）时，resourcepacks 下的都算；Paxi 包总是加载。"""
    root = Path(root)
    if options_text is None:
        opts = root / "options.txt"
        options_text = opts.read_bytes().decode("utf-8", "replace") if opts.is_file() else ""
    paxi = _list(root / PAXI_DIR)
    packs = read_packs(options_text)
    if packs is None:
        return _list(root / "resourcepacks") + paxi
    out: list[Path] = []
    for pid in reversed(packs):
        path = resolve_pack(root, pid)
        if path is not None and path not in out:
            out.append(path)
    return out + [p for p in paxi if p not in out]


def read_pack(path: Path, warnings: list[str]) -> Patch | None:
    """读取资源包的文件列表与 zh_cn 语言文件；无法读取时返回 None。"""
    patch = Patch(path.name)

    def add(name: str, read) -> None:
        name = name.replace("\\", "/")
        if not name.lower().startswith("assets/"):
            return
        patch.files.add(name.lower())
        m = ZH_LANG_RE.match(name)
        if m:
            try:
                entries = parse_any(read(), m.group(2))
            except (ValueError, OSError) as exc:
                warnings.append(f"汉化补丁 {path.name} 中的语言文件解析失败，已跳过：{name}（{exc}）")
                return
            patch.lang.setdefault(m.group(1), {}).update(entries)

    try:
        if path.is_dir():
            for f in path.rglob("*"):
                if f.is_file():
                    add(f.relative_to(path).as_posix(), f.read_bytes)
        else:
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        add(info.filename, lambda n=info.filename: z.read(n))
    except (OSError, zipfile.BadZipFile) as exc:
        warnings.append(f"资源包 {path.name} 无法读取，已跳过：{exc}")
        return None
    return patch


def installed_patches(root: Path, options_text: str | None = None,
                      warnings: list[str] | None = None) -> list[Patch]:
    """已启用的、含 zh_cn 语言文件的资源包（汉化补丁），按优先级从高到低。"""
    warnings = warnings if warnings is not None else []
    found = (read_pack(path, warnings) for path in enabled_packs(root, options_text))
    return [p for p in found if p is not None and p.lang]


class PatchIndex:
    def __init__(self, patches: list[Patch]) -> None:
        self.patches = patches
        self._data: dict[str, dict[str, str]] = {}
        for patch in patches:  # 高优先级在前，先到先得
            for ns, entries in patch.lang.items():
                bucket = self._data.setdefault(ns, {})
                for key, text in entries.items():
                    bucket.setdefault(key, text)

    def lookup(self, namespace: str, key: str) -> str | None:
        return self._data.get(namespace, {}).get(key)

    def __len__(self) -> int:
        return sum(len(b) for b in self._data.values())

    def non_lang_files(self) -> set[str]:
        """补丁提供的非语言文件（如手册、任务文本）：整文件覆盖，应以补丁为准。"""
        return {f for p in self.patches for f in p.files if not LANG_RE.match(f)}
