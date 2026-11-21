"""JS（KubeJS）/ ZS（CraftTweaker）脚本的轻量字符串扫描。

不做完整语法分析，只追踪括号嵌套以确定每个字符串字面量所在的调用，
并跳过注释、JS 正则字面量和含插值的模板字符串。
"""
from __future__ import annotations

from dataclasses import dataclass

IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.")
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of"}
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
ENCODE_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


@dataclass
class StrLit:
    value: str
    start: int
    end: int
    quote: str
    callee: str | None = None


def _ident_before(text: str, i: int) -> str:
    j = i - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    k = j
    while k >= 0 and text[k] in IDENT_CHARS:
        k -= 1
    return text[k + 1:j + 1]


def _callee(text: str, start: int, stack: list[tuple[str, str]]) -> str | None:
    j = start - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j >= 0 and text[j] == "=" and (j == 0 or text[j - 1] not in "=!<>+-*/"):
        name = _ident_before(text, j)
        if name:
            return "=" + name.rsplit(".", 1)[-1]
    if stack and stack[-1][0] == "(":
        return stack[-1][1] or None
    return None


def _read_string(text: str, i: int, quote: str) -> tuple[StrLit | None, int]:
    """从 text[i]（引号）开始读取字符串，返回 (字面量或 None, 结束位置)。"""
    n = len(text)
    j = i + 1
    buf: list[str] = []
    has_expr = False
    while j < n:
        c = text[j]
        if c == "\\" and j + 1 < n:
            e = text[j + 1]
            if e in SIMPLE_ESCAPES:
                buf.append(SIMPLE_ESCAPES[e])
                j += 2
            elif e == "x" and j + 3 < n:
                buf.append(chr(int(text[j + 2:j + 4], 16)))
                j += 4
            elif e == "u" and j + 5 < n and text[j + 2] != "{":
                buf.append(chr(int(text[j + 2:j + 6], 16)))
                j += 6
            elif e == "\n":
                j += 2
            else:
                buf.append(e)
                j += 2
            continue
        if c == quote:
            return (None if has_expr else StrLit("".join(buf), i, j + 1, quote)), j + 1
        if c == "\n" and quote != "`":
            return None, j  # 未闭合，跳过
        if quote == "`" and c == "$" and j + 1 < n and text[j + 1] == "{":
            has_expr = True
            depth = 0
            j += 1
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            j += 1
            continue
        buf.append(c)
        j += 1
    return None, n


def _skip_regex(text: str, i: int) -> int:
    n = len(text)
    j = i + 1
    in_class = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return j
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and text[j].isalpha():
                j += 1
            return j
        j += 1
    return n


def scan(text: str, js: bool) -> list[StrLit]:
    out: list[StrLit] = []
    stack: list[tuple[str, str]] = []
    n = len(text)
    i = 0
    prev = ""
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i) or (not js and c == "#"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'" or (js and c == "`"):
            lit, i = _read_string(text, i, c)
            if lit is not None:
                lit.callee = _callee(text, lit.start, stack)
                out.append(lit)
            prev = c
            continue
        if js and c == "/" and (prev == "" or prev in REGEX_PREV or _ident_before(text, i) in REGEX_KEYWORDS):
            i = _skip_regex(text, i)
            prev = "/"
            continue
        if c == "(":
            stack.append(("(", _ident_before(text, i)))
        elif c in "[{":
            stack.append((c, ""))
        elif c in ")]}" and stack:
            stack.pop()
        prev = c
        i += 1
    return out


def encode(value: str, quote: str) -> str:
    body = "".join(ENCODE_ESCAPES.get(ch, "\\" + ch if ch == quote else ch) for ch in value)
    return quote + body + quote
