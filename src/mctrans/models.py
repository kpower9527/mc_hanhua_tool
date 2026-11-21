from __future__ import annotations

from dataclasses import dataclass

ORIGINS = ("mod_builtin", "cfpa", "memory", "llm", "human")
STATUSES = ("pending", "done", "failed", "confirmed", "skipped")


@dataclass
class Entry:
    """从整合包中提取的一条待翻译文本。"""

    handler: str
    source: str
    locator: str
    namespace: str
    original: str
    context: str = ""
    existing_zh: str | None = None
    file_hash: str = ""
    id: int | None = None


@dataclass
class TranslatedItem:
    """条目及其当前采用的译文。"""

    entry: Entry
    text: str
    origin: str
    status: str
