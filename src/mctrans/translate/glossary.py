from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

WORD_RE = re.compile(r"[a-z0-9']+")
MIN_TERM_LEN = 3
MAX_TERM_WORDS = 6


class Glossary:
    """英文术语 → 中文译名，按整词序列匹配。"""

    def __init__(self) -> None:
        self._terms: dict[tuple[str, ...], tuple[str, str]] = {}
        self._index: dict[str, set[tuple[str, ...]]] = {}

    def __len__(self) -> int:
        return len(self._terms)

    def add(self, en: str, zh: str, override: bool = False) -> None:
        en, zh = en.strip(), zh.strip()
        if len(en) < MIN_TERM_LEN or not zh or zh == en:
            return
        if any(c in en or c in zh for c in ("§", "%", "{", "$(")):
            return
        words = tuple(WORD_RE.findall(en.lower()))
        if not words or len(words) > MAX_TERM_WORDS:
            return
        if words in self._terms and not override:
            return
        self._terms[words] = (en, zh)
        self._index.setdefault(words[0], set()).add(words)

    def find(self, texts: Iterable[str], limit: int = 60) -> list[tuple[str, str]]:
        hits: set[tuple[str, ...]] = set()
        for text in texts:
            words = WORD_RE.findall(text.lower())
            for i, word in enumerate(words):
                for cand in self._index.get(word, ()):
                    if tuple(words[i:i + len(cand)]) == cand:
                        hits.add(cand)
        ordered = sorted(hits, key=lambda c: (-len(c), c))[:limit]
        return [self._terms[c] for c in ordered]


def build_glossary(workspace_pairs: list[tuple[str, str]], user_pairs: list[tuple[str, str]]) -> Glossary:
    g = Glossary()
    for en, zh in user_pairs:
        g.add(en, zh, override=True)
    for en, zh in workspace_pairs:
        g.add(en, zh)
    return g


def load_user_glossary(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
            continue
        en, zh = line.split("\t", 1)
        if en.strip() and zh.strip():
            pairs.append((en.strip(), zh.strip()))
    return pairs
