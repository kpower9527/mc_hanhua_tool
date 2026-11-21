from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
I18N_MODID_RE = re.compile(r'(?:modId\s*=\s*"i18nupdatemod"|"id"\s*:\s*"i18nupdatemod")')


class InstanceError(Exception):
    pass


@dataclass
class Instance:
    root: Path
    mc_version: str
    loader: str
    mod_jars: list[Path] = field(default_factory=list)
    has_i18nupdatemod: bool = False


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _from_mmc_pack(root: Path) -> str | None:
    for path in (root / "mmc-pack.json", root.parent / "mmc-pack.json"):
        data = _read_json(path)
        if isinstance(data, dict):
            for comp in data.get("components", []) or []:
                if isinstance(comp, dict) and comp.get("uid") == "net.minecraft" and comp.get("version"):
                    return str(comp["version"])
    return None


def _from_curseforge(root: Path) -> str | None:
    data = _read_json(root / "minecraftinstance.json")
    if isinstance(data, dict) and isinstance(data.get("gameVersion"), str) and data["gameVersion"]:
        return data["gameVersion"]
    return None


def _from_launcher_json(root: Path) -> str | None:
    data = _read_json(root / f"{root.name}.json")
    if not isinstance(data, dict):
        return None
    for patch in data.get("patches", []) or []:
        if isinstance(patch, dict) and patch.get("id") == "game" and patch.get("version"):
            return str(patch["version"])
    game_args = (data.get("arguments") or {}).get("game") or []
    for i, arg in enumerate(game_args):
        if arg == "--fml.mcVersion" and i + 1 < len(game_args) and isinstance(game_args[i + 1], str):
            return game_args[i + 1]
    for lib in data.get("libraries", []) or []:
        name = lib.get("name", "") if isinstance(lib, dict) else ""
        if name.startswith("net.fabricmc:intermediary:"):
            return name.rsplit(":", 1)[1]
    for key in ("clientVersion", "inheritsFrom", "id"):
        value = data.get(key)
        if isinstance(value, str) and VERSION_RE.match(value):
            return value
    return None


def detect_mc_version(root: Path) -> str | None:
    for finder in (_from_mmc_pack, _from_curseforge, _from_launcher_json):
        version = finder(root)
        if version:
            return version
    return None


def inspect_jars(jars: list[Path]) -> tuple[str, bool]:
    votes: Counter[str] = Counter()
    has_i18n = False
    for jar in jars:
        if "i18nupdate" in jar.name.lower():
            has_i18n = True
        try:
            with zipfile.ZipFile(jar) as z:
                names = set(z.namelist())
                meta = ""
                for candidate in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml", "fabric.mod.json", "quilt.mod.json"):
                    if candidate in names:
                        meta = z.read(candidate).decode("utf-8", errors="replace")
                        break
        except (zipfile.BadZipFile, OSError):
            continue
        if I18N_MODID_RE.search(meta):
            has_i18n = True
        if "META-INF/neoforge.mods.toml" in names:
            votes["neoforge"] += 1
        elif "fabric.mod.json" in names or "quilt.mod.json" in names:
            votes["fabric"] += 1
        elif "META-INF/mods.toml" in names:
            votes["forge"] += 1
    loader = votes.most_common(1)[0][0] if votes else "unknown"
    return loader, has_i18n


def load_instance(root: Path, mc_version: str | None = None) -> Instance:
    root = Path(root).resolve()
    if not (root / "mods").is_dir():
        raise InstanceError(f"{root} 下没有 mods 目录，请指定游戏实例目录（含 mods/、config/ 的那一层）")
    version = mc_version or detect_mc_version(root)
    if not version:
        raise InstanceError("无法自动识别 Minecraft 版本，请用 --mc-version 指定，例如 --mc-version 1.20.1")
    jars = sorted(p for p in (root / "mods").glob("*.jar") if p.is_file())
    loader, has_i18n = inspect_jars(jars)
    return Instance(root=root, mc_version=version, loader=loader, mod_jars=jars, has_i18nupdatemod=has_i18n)
