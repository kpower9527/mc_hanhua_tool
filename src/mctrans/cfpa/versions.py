from __future__ import annotations

from dataclasses import dataclass

from ..mcversion import parse_version

# (最低 MC 版本, CFPA 版本线, 是否有 Fabric 包)
CFPA_LINES: list[tuple[tuple[int, int], str, bool]] = [
    ((1, 16), "1.16", True),
    ((1, 18), "1.18", True),
    ((1, 19), "1.19", False),
    ((1, 20), "1.20", True),
    ((1, 21), "1.21", True),
    ((26, 1), "26.1", True),
]
FABRIC_LOADERS = ("fabric", "quilt")


@dataclass(frozen=True)
class CfpaAsset:
    key: str
    zip_name: str
    md5_name: str


def _asset(line: str, fabric: bool) -> CfpaAsset:
    dashed = line.replace(".", "-")
    if fabric:
        return CfpaAsset(f"{line}-fabric", f"Minecraft-Mod-Language-Modpack-{dashed}-Fabric.zip", f"{line}-fabric.md5")
    return CfpaAsset(line, f"Minecraft-Mod-Language-Modpack-{dashed}.zip", f"{line}.md5")


def cfpa_assets(mc_version: str, loader: str) -> list[CfpaAsset]:
    v = parse_version(mc_version)[:2]
    chosen: tuple[str, bool] | None = None
    for min_version, line, has_fabric in CFPA_LINES:
        if v >= min_version:
            chosen = (line, has_fabric)
    if chosen is None:
        return []
    line, has_fabric = chosen
    forge = _asset(line, fabric=False)
    if not has_fabric:
        return [forge]
    fabric = _asset(line, fabric=True)
    return [fabric, forge] if loader in FABRIC_LOADERS else [forge, fabric]
