"""FTB Library 风格 SNBT 的解析与生成。

解析结果记录每个字符串字面量在原文中的位置，便于只替换文本、保持文件其余部分不变。
转义规则与 FTB Library 的 SNBTParser 一致：只认 \\" \\\\ \\t \\b \\n \\r \\f，未知转义连同反斜杠一起丢弃。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DECODE = {'"': '"', "\\": "\\", "t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f"}
ENCODE = {'"': '"', "\\": "\\", "\t": "t", "\b": "b", "\n": "n", "\r": "r", "\f": "f"}
BARE_STOP = set(" \t\r\n,:{}[]")
SIMPLE_KEY_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


class SNBTError(ValueError):
    pass


@dataclass
class SString:
    value: str
    start: int
    end: int


@dataclass
class Scalar:
    raw: str


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)

    def error(self, message: str) -> SNBTError:
        line = self.s.count("\n", 0, self.i) + 1
        return SNBTError(f"SNBT 格式错误（第 {line} 行）：{message}")

    def skip(self) -> None:
        while self.i < self.n and (self.s[self.i].isspace() or self.s[self.i] == ","):
            self.i += 1

    def peek(self) -> str:
        if self.i >= self.n:
            raise self.error("意外的文件结尾")
        return self.s[self.i]

    def parse(self):
        self.skip()
        value = self.value()
        self.skip()
        if self.i != self.n:
            raise self.error("多余的内容")
        return value

    def value(self):
        c = self.peek()
        if c == "{":
            return self.compound()
        if c == "[":
            return self.list()
        if c in "\"'":
            return self.string()
        return Scalar(self.bare())

    def compound(self) -> dict:
        self.i += 1
        out: dict = {}
        while True:
            self.skip()
            if self.peek() == "}":
                self.i += 1
                return out
            key = self.string().value if self.peek() in "\"'" else self.bare()
            self.skip()
            if self.peek() != ":":
                raise self.error(f"键 {key} 后缺少冒号")
            self.i += 1
            self.skip()
            out[key] = self.value()

    def list(self) -> list:
        self.i += 1
        self.skip()
        if self.i + 1 < self.n and self.s[self.i] in "BILbil" and self.s[self.i + 1] == ";":
            self.i += 2
        out: list = []
        while True:
            self.skip()
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value())

    def string(self) -> SString:
        quote_char = self.s[self.i]
        start = self.i
        self.i += 1
        buf: list[str] = []
        while True:
            if self.i >= self.n:
                raise self.error("字符串没有闭合")
            c = self.s[self.i]
            self.i += 1
            if c == "\n":
                raise self.error("字符串中出现换行")
            if c == "\\":
                if self.i >= self.n:
                    raise self.error("字符串没有闭合")
                e = self.s[self.i]
                self.i += 1
                if e in DECODE:
                    buf.append(DECODE[e])
                continue
            if c == quote_char:
                return SString("".join(buf), start, self.i)
            buf.append(c)

    def bare(self) -> str:
        start = self.i
        while self.i < self.n and self.s[self.i] not in BARE_STOP:
            self.i += 1
        if self.i == start:
            raise self.error(f"无法识别的字符 {self.s[self.i]!r}" if self.i < self.n else "意外的文件结尾")
        return self.s[start:self.i]


def parse(text: str):
    return _Parser(text).parse()


def quote(value: str) -> str:
    return '"' + "".join("\\" + ENCODE[ch] if ch in ENCODE else ch for ch in value) + '"'


def to_lang_value(node) -> str | list[str] | None:
    if isinstance(node, SString):
        return node.value
    if isinstance(node, list) and all(isinstance(x, SString) for x in node):
        return [x.value for x in node]
    return None


def dump_lang(table: dict[str, str | list[str]]) -> str:
    lines = ["{"]
    for key, value in table.items():
        k = key if SIMPLE_KEY_RE.match(key) else quote(key)
        if isinstance(value, list):
            if not value:
                lines.append(f"\t{k}: []")
            else:
                lines.append(f"\t{k}: [")
                lines.extend(f"\t\t{quote(v)}" for v in value)
                lines.append("\t]")
        else:
            lines.append(f"\t{k}: {quote(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def replace_spans(text: str, replacements: list[tuple[int, int, str]]) -> str:
    out = text
    for start, end, value in sorted(replacements, key=lambda r: r[0], reverse=True):
        out = out[:start] + quote(value) + out[end:]
    return out
