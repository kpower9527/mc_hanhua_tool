from __future__ import annotations

import io
import json
import zipfile

from ..mcversion import parse_version

# (最低版本, pack_format)，参考 Minecraft Wiki 的 pack format 列表
PACK_FORMATS: list[tuple[tuple[int, int, int], int]] = [
    ((1, 6, 0), 1), ((1, 9, 0), 2), ((1, 11, 0), 3), ((1, 13, 0), 4), ((1, 15, 0), 5),
    ((1, 16, 2), 6), ((1, 17, 0), 7), ((1, 18, 0), 8),
    ((1, 19, 0), 9), ((1, 19, 3), 12), ((1, 19, 4), 13), ((1, 20, 0), 15),
    ((1, 20, 2), 18), ((1, 20, 3), 22), ((1, 20, 5), 32), ((1, 21, 0), 34),
    ((1, 21, 2), 42), ((1, 21, 4), 46), ((1, 21, 5), 55), ((1, 21, 6), 63),
    ((1, 21, 7), 64), ((1, 21, 9), 69),
]
MAX_FORMAT = 9999


def pack_format_for(mc_version: str) -> int:
    v = parse_version(mc_version)
    fmt = None
    for min_version, f in PACK_FORMATS:
        if v >= min_version:
            fmt = f
    if fmt is None:
        raise ValueError(f"不支持的 Minecraft 版本：{mc_version}（仅支持 1.6 及以上）")
    return fmt


def pack_mcmeta(mc_version: str, description: str) -> bytes:
    fmt = pack_format_for(mc_version)
    pack: dict = {"pack_format": fmt, "description": description}
    if fmt >= 18:  # 1.20.2+ 支持 supported_formats，声明兼容所有更高版本
        pack["supported_formats"] = [fmt, MAX_FORMAT]
    if fmt >= 65:  # 1.21.9+ 改用 min_format / max_format
        pack["min_format"] = fmt
        pack["max_format"] = MAX_FORMAT
    return json.dumps({"pack": pack}, ensure_ascii=False, indent=2).encode("utf-8")


def build_pack_zip(files: dict[str, bytes], mcmeta: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in [("pack.mcmeta", mcmeta)] + sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()
