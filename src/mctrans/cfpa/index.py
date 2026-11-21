from __future__ import annotations

import io
import re
import zipfile
from collections import Counter

from ..handlers.lang_json import parse_lang

ZH_LANG_RE = re.compile(r"^assets/([^/]+)/lang/zh_cn\.json$", re.IGNORECASE)
PRINTF_RE = re.compile(r"%(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?([a-zA-Z])")
BRACE_RE = re.compile(r"\{[A-Za-z0-9_.:-]*\}")


def _signature(text: str) -> Counter:
    sig: Counter = Counter("%" + m.group(1) for m in PRINTF_RE.finditer(text.replace("%%", "")))
    sig.update(BRACE_RE.findall(text))
    return sig


def compatible(original: str, translated: str) -> bool:
    """原文与译文的参数（%s、%1$d、{0} 等）类型和数量一致。用于发现过时的 CFPA 译文。"""
    return _signature(original) == _signature(translated)


class CfpaIndex:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self.warnings: list[str] = []

    @classmethod
    def from_zips(cls, zips: list[bytes]) -> "CfpaIndex":
        idx = cls()
        for data in zips:
            idx._add_zip(data)
        return idx

    def _add_zip(self, data: bytes) -> None:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            self.warnings.append("CFPA 资源包已损坏，已跳过")
            return
        with z:
            for name in z.namelist():
                m = ZH_LANG_RE.match(name)
                if not m:
                    continue
                try:
                    entries = parse_lang(z.read(name))
                except ValueError as exc:
                    self.warnings.append(f"CFPA 语言文件解析失败，已跳过：{name}（{exc}）")
                    continue
                bucket = self._data.setdefault(m.group(1), {})
                for key, text in entries.items():
                    bucket.setdefault(key, text)

    def lookup(self, namespace: str, key: str) -> str | None:
        return self._data.get(namespace, {}).get(key)

    def __len__(self) -> int:
        return sum(len(b) for b in self._data.values())

    @property
    def namespace_count(self) -> int:
        return len(self._data)
