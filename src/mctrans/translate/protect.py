from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

TOKEN_FMT = "⟦{}⟧"
TOKEN_RE = re.compile(r"⟦(\d+)⟧")

_FORMAT_PATTERNS = [
    r"§[0-9a-fk-orA-FK-OR]",                       # 颜色/样式码
    r"&[0-9a-fk-or](?![a-z])",                     # & 颜色码（后面紧跟小写字母时视为普通文字，如 Rock&roll）
    r"%(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?[a-zA-Z%]", # printf 参数
    r"\{[^{}\n]*\}",                               # {0} {player} {@pagebreak} {image:...}
    r"\$\([^)]*\)",                                # Patchouli 宏
    r"\\n",                                        # 字面量 \n（反斜杠 + n）
    r"\n",                                         # 真实换行
]
PROTECT_RE = re.compile("|".join(_FORMAT_PATTERNS))
# 英文原文里的私用区字符与汉字：整合包常用自定义字体把它们显示成图标（如 §f陋§r Arcane Power）。
# 发给模型时一并保护、要求原样保留；比较人工译文的格式码时不算（译文本身就是汉字）
GLYPH_PATTERN = r"[\ue000-\uf8ff\u3400-\u9fff\uf900-\ufaff]+"
PROTECT_GLYPHS_RE = re.compile("|".join(_FORMAT_PATTERNS + [GLYPH_PATTERN]))


class TokenMismatch(ValueError):
    pass


@dataclass
class Masked:
    text: str
    tokens: list[str]


def protect(text: str, glyphs: bool = False) -> Masked:
    tokens: list[str] = []

    def repl(m: re.Match) -> str:
        tokens.append(m.group(0))
        return TOKEN_FMT.format(len(tokens) - 1)

    return Masked((PROTECT_GLYPHS_RE if glyphs else PROTECT_RE).sub(repl, text), tokens)


# 只影响外观的格式码：颜色/样式码与换行。缺少或多出只会让颜色、分段不完全一致，不会出错
COSMETIC_RE = re.compile(r"^(?:§.|&[0-9a-fk-or]|\\n|\n)$")


def _is_cosmetic(masked: Masked, index: str) -> bool:
    i = int(index)
    return i < len(masked.tokens) and bool(COSMETIC_RE.match(masked.tokens[i]))


def restore(translated: str, masked: Masked, allow_cosmetic_mismatch: bool = False) -> str:
    """把占位符还原为格式码。

    allow_cosmetic_mismatch=True 时，若差异只在颜色/样式码和换行（参数 %s、{0}、Patchouli 宏必须完全一致），
    仍然接受，用于多次重试仍失败的长段落。
    """
    found = Counter(TOKEN_RE.findall(translated))
    expected = Counter(str(i) for i in range(len(masked.tokens)))
    if found != expected:
        diff = list(((expected - found) + (found - expected)).elements())
        if not (allow_cosmetic_mismatch and all(_is_cosmetic(masked, i) for i in diff)):
            raise TokenMismatch(
                f"占位符不一致：期望 {sorted(expected.elements())}，实际 {sorted(found.elements())}"
            )
    return TOKEN_RE.sub(lambda m: masked.tokens[int(m.group(1))], translated)


def token_differences(translated: str, masked: Masked) -> tuple[list[str], list[str]]:
    """返回 (缺少的格式码, 多出的格式码)。"""
    found = Counter(TOKEN_RE.findall(translated))
    expected = Counter(str(i) for i in range(len(masked.tokens)))

    def names(counter: Counter) -> list[str]:
        return [masked.tokens[int(i)] for i in sorted(counter.elements(), key=int) if int(i) < len(masked.tokens)]

    return names(expected - found), names(found - expected)


def has_translatable_text(masked_text: str) -> bool:
    return re.search(r"[A-Za-z]", TOKEN_RE.sub("", masked_text)) is not None
