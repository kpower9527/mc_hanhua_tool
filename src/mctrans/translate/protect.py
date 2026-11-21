from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

TOKEN_FMT = "⟦{}⟧"
TOKEN_RE = re.compile(r"⟦(\d+)⟧")

PROTECT_RE = re.compile("|".join([
    r"§[0-9a-fk-orA-FK-OR]",                       # 颜色/样式码
    r"&[0-9a-fk-or](?![a-z])",                     # & 颜色码（后面紧跟小写字母时视为普通文字，如 Rock&roll）
    r"%(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?[a-zA-Z%]", # printf 参数
    r"\{[^{}\n]*\}",                               # {0} {player} {@pagebreak} {image:...}
    r"\$\([^)]*\)",                                # Patchouli 宏
    r"\n",                                        # 字面量 \n
    r"\n",                                         # 真实换行
]))


class TokenMismatch(ValueError):
    pass


@dataclass
class Masked:
    text: str
    tokens: list[str]


def protect(text: str) -> Masked:
    tokens: list[str] = []

    def repl(m: re.Match) -> str:
        tokens.append(m.group(0))
        return TOKEN_FMT.format(len(tokens) - 1)

    return Masked(PROTECT_RE.sub(repl, text), tokens)


def restore(translated: str, masked: Masked) -> str:
    found = Counter(TOKEN_RE.findall(translated))
    expected = Counter(str(i) for i in range(len(masked.tokens)))
    if found != expected:
        raise TokenMismatch(
            f"占位符不一致：期望 {sorted(expected.elements())}，实际 {sorted(found.elements())}"
        )
    return TOKEN_RE.sub(lambda m: masked.tokens[int(m.group(1))], translated)


def has_translatable_text(masked_text: str) -> bool:
    return re.search(r"[A-Za-z]", TOKEN_RE.sub("", masked_text)) is not None
