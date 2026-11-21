from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import home_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    namespace TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    original TEXT NOT NULL,
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    updated_at TEXT,
    PRIMARY KEY (namespace, source_hash)
);
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class MemoryRecord:
    namespace: str
    original: str
    text: str
    origin: str
    confirmed: bool = False
    model: str | None = None


class Memory:
    """跨整合包的全局翻译记忆库，以 (namespace, 原文哈希) 为键。"""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @classmethod
    def default(cls) -> "Memory":
        return cls(home_dir() / "memory.db")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def remember(self, records: list[MemoryRecord]) -> int:
        written = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.conn:
            for r in records:
                key = (r.namespace, _hash(r.original))
                row = self.conn.execute(
                    "SELECT text, confirmed FROM memory WHERE namespace=? AND source_hash=?", key
                ).fetchone()
                if row is not None:
                    if row["confirmed"] and not r.confirmed:
                        continue
                    if row["text"] == r.text and bool(row["confirmed"]) == r.confirmed:
                        continue
                self.conn.execute(
                    "INSERT INTO memory(namespace, source_hash, original, text, origin, confirmed, model, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(namespace, source_hash) DO UPDATE SET text=excluded.text, origin=excluded.origin, "
                    "confirmed=excluded.confirmed, model=excluded.model, updated_at=excluded.updated_at",
                    (*key, r.original, r.text, r.origin, int(r.confirmed), r.model, now),
                )
                written += 1
        return written

    def lookup(self, namespace: str, original: str) -> MemoryRecord | None:
        row = self.conn.execute(
            "SELECT * FROM memory WHERE namespace=? AND source_hash=?", (namespace, _hash(original))
        ).fetchone()
        if row is None:
            return None
        return MemoryRecord(row["namespace"], row["original"], row["text"], row["origin"],
                            bool(row["confirmed"]), row["model"])

    def __len__(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
