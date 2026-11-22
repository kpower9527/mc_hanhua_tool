"""压缩发送给模型的提示（hint）。

hint 只用来让模型判断文本类型（物品名、提示、说明……）。原始键名往往比原文还长
（实测语言文件的键名总字数超过原文），而且含有与翻译无关的部分，所以发送前精简：
- 去掉 FTB 任务书键名中的 16 位十六进制 ID（chapter.05E614FDA677D85E.title → chapter.title）
- 去掉与模组命名空间相同的一段（item.mekanism.steel_ingot → item.steel_ingot）
- 最后一段只是原文的蛇形写法时省略（item.steel_ingot + "Steel Ingot" → item）
"""
from __future__ import annotations

import re

HEX_ID_RE = re.compile(r"(?<![0-9A-Za-z])[0-9A-F]{16}(?![0-9A-Za-z])")
MAX_HINT = 60


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def compact_hint(hint: str, original: str, namespace: str = "") -> str:
    if not hint:
        return ""
    h = HEX_ID_RE.sub("", hint)
    if "#" in h and "/" in h.split("#", 1)[0]:  # Patchouli 等：文件路径#字段 → 只留所在目录与字段
        path, field = h.split("#", 1)
        parts = [p for p in path.removesuffix(".json").split("/") if p and p != "en_us"]
        if parts and _norm(parts[-1]) == _norm(original):
            parts = parts[:-1]
        h = "/".join(parts[-2:]) + "#" + field
    elif "." in h and " " not in h and "/" not in h:  # 形如语言文件键名
        parts = [p for p in h.split(".") if p]
        if namespace and len(parts) > 2:
            parts = [p for p in parts if p != namespace] or parts
        if len(parts) > 1 and _norm(parts[-1]) and _norm(parts[-1]) == _norm(original):
            parts = parts[:-1]
        h = ".".join(parts)
    else:
        h = re.sub(r"\.{2,}", ".", h)
    if len(h) > MAX_HINT:
        h = "…" + h[-(MAX_HINT - 1):]
    return h
