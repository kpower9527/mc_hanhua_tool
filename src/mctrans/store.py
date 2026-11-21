from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .models import Entry, TranslatedItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handler TEXT NOT NULL,
    source TEXT NOT NULL,
    locator TEXT NOT NULL,
    namespace TEXT NOT NULL,
    original TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    existing_zh TEXT,
    file_hash TEXT NOT NULL DEFAULT '',
    stale INTEGER NOT NULL DEFAULT 0,
    UNIQUE(handler, source, locator)
);
CREATE TABLE IF NOT EXISTS translations (
    entry_id INTEGER PRIMARY KEY REFERENCES entries(id),
    text TEXT,
    origin TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    model TEXT,
    note TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    params TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats TEXT
);
"""

GLOSSARY_PREFIXES = ("item.", "block.", "entity.", "fluid.", "effect.", "enchantment.", "biome.")
CHANGED_NOTE = "原文已变更，需复核"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SyncStats:
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    stale: int = 0


@dataclass
class TranslationUpdate:
    entry_id: int
    text: str | None
    origin: str | None
    status: str
    model: str | None = None
    note: str | None = None


@dataclass
class ReviewRow:
    id: int
    handler: str
    source: str
    locator: str
    original: str
    text: str | None
    origin: str | None
    status: str
    note: str | None


class Workspace:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @classmethod
    def for_instance(cls, root: Path) -> "Workspace":
        return cls(Path(root) / ".mctrans" / "workspace.db")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- meta ----
    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # ---- entries ----
    def sync_entries(self, handler: str, entries: Iterable[Entry]) -> SyncStats:
        stats = SyncStats()
        seen: set[int] = set()
        with self.conn:
            for e in entries:
                row = self.conn.execute(
                    "SELECT id, original FROM entries WHERE handler=? AND source=? AND locator=?",
                    (handler, e.source, e.locator),
                ).fetchone()
                if row is None:
                    cur = self.conn.execute(
                        "INSERT INTO entries(handler, source, locator, namespace, original, context, existing_zh, file_hash) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (handler, e.source, e.locator, e.namespace, e.original, e.context, e.existing_zh, e.file_hash),
                    )
                    eid = cur.lastrowid
                    self.conn.execute(
                        "INSERT INTO translations(entry_id, status, updated_at) VALUES(?, 'pending', ?)",
                        (eid, _now()),
                    )
                    stats.added += 1
                else:
                    eid = row["id"]
                    self.conn.execute(
                        "UPDATE entries SET namespace=?, original=?, context=?, existing_zh=?, file_hash=?, stale=0 WHERE id=?",
                        (e.namespace, e.original, e.context, e.existing_zh, e.file_hash, eid),
                    )
                    if row["original"] != e.original:
                        tr = self.conn.execute(
                            "SELECT status, origin FROM translations WHERE entry_id=?", (eid,)
                        ).fetchone()
                        reviewed = tr is not None and (tr["status"] == "confirmed" or tr["origin"] == "human")
                        self.conn.execute(
                            "UPDATE translations SET status='pending', note=?, updated_at=? WHERE entry_id=?",
                            (CHANGED_NOTE if reviewed else None, _now(), eid),
                        )
                        stats.changed += 1
                    else:
                        stats.unchanged += 1
                seen.add(eid)
            active = [r["id"] for r in self.conn.execute(
                "SELECT id FROM entries WHERE handler=? AND stale=0", (handler,))]
            gone = [i for i in active if i not in seen]
            self.conn.executemany("UPDATE entries SET stale=1 WHERE id=?", [(i,) for i in gone])
            stats.stale = len(gone)
        return stats

    def apply_builtin(self) -> int:
        """把 mod 自带的中文作为译文（优先级高于机翻，低于人工）。"""
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE translations
                SET text=(SELECT existing_zh FROM entries e WHERE e.id=translations.entry_id),
                    origin='mod_builtin', status='done', model=NULL, note=NULL, updated_at=?
                WHERE entry_id IN (
                    SELECT e.id FROM entries e JOIN translations t ON t.entry_id=e.id
                    WHERE e.stale=0
                      AND e.existing_zh IS NOT NULL AND e.existing_zh != ''
                      AND e.existing_zh != e.original
                      AND t.status != 'confirmed'
                      AND COALESCE(t.origin, '') != 'human'
                      AND NOT (COALESCE(t.origin, '')='mod_builtin' AND t.status='done'
                               AND COALESCE(t.text, '')=e.existing_zh)
                )
                """,
                (_now(),),
            )
            return cur.rowcount

    @staticmethod
    def _entry(row: sqlite3.Row) -> Entry:
        return Entry(
            handler=row["handler"], source=row["source"], locator=row["locator"],
            namespace=row["namespace"], original=row["original"], context=row["context"],
            existing_zh=row["existing_zh"], file_hash=row["file_hash"], id=row["id"],
        )

    def pending_entries(self) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT e.* FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status IN ('pending', 'failed') ORDER BY e.id"
        )
        return [self._entry(r) for r in rows]

    # ---- translations ----
    def save_translations(self, updates: list[TranslationUpdate]) -> int:
        written = 0
        with self.conn:
            for u in updates:
                tr = self.conn.execute(
                    "SELECT status FROM translations WHERE entry_id=?", (u.entry_id,)
                ).fetchone()
                if tr is None:
                    raise KeyError(f"条目不存在：{u.entry_id}")
                if tr["status"] == "confirmed" and u.origin != "human":
                    continue
                self.conn.execute(
                    "UPDATE translations SET text=?, origin=?, status=?, model=?, note=?, updated_at=? WHERE entry_id=?",
                    (u.text, u.origin, u.status, u.model, u.note, _now(), u.entry_id),
                )
                written += 1
        return written

    def translated_items(self, handler: str | None = None) -> list[TranslatedItem]:
        sql = (
            "SELECT e.*, t.text AS t_text, t.origin AS t_origin, t.status AS t_status "
            "FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status IN ('done', 'confirmed') AND t.text IS NOT NULL"
        )
        params: tuple = ()
        if handler:
            sql += " AND e.handler=?"
            params = (handler,)
        sql += " ORDER BY e.id"
        return [
            TranslatedItem(entry=self._entry(r), text=r["t_text"], origin=r["t_origin"], status=r["t_status"])
            for r in self.conn.execute(sql, params)
        ]

    def fill_from(self, origin: str, lookup: Callable[[Entry], str | None],
                  overridable: frozenset[str] = frozenset(), handler: str | None = None) -> int:
        """用外部来源（CFPA、记忆库等）填充译文，遵守优先级：只覆盖待翻译条目和 overridable 中的来源。"""
        sql = (
            "SELECT e.*, t.text AS t_text, t.origin AS t_origin, t.status AS t_status "
            "FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status != 'confirmed'"
        )
        params: tuple = ()
        if handler:
            sql += " AND e.handler=?"
            params = (handler,)
        updates: list[TranslationUpdate] = []
        for r in self.conn.execute(sql, params).fetchall():
            status, current = r["t_status"], r["t_origin"]
            if not (status in ("pending", "failed") or (status == "done" and current in overridable)):
                continue
            text = lookup(self._entry(r))
            if not text or (status == "done" and current == origin and r["t_text"] == text):
                continue
            updates.append(TranslationUpdate(r["id"], text, origin, "done"))
        return self.save_translations(updates)

    def glossary_pairs(self) -> list[tuple[str, str]]:
        return [
            (item.entry.original, item.text)
            for item in self.translated_items("lang_json")
            if item.entry.locator.startswith(GLOSSARY_PREFIXES)
        ]

    _REVIEW_SQL = (
        "SELECT e.id, e.handler, e.source, e.locator, e.original, t.text, t.origin, t.status, t.note "
        "FROM entries e JOIN translations t ON t.entry_id=e.id WHERE e.stale=0 AND t.status != 'skipped'"
    )

    @staticmethod
    def _review_row(r: sqlite3.Row) -> ReviewRow:
        return ReviewRow(r["id"], r["handler"], r["source"], r["locator"], r["original"],
                         r["text"], r["origin"], r["status"], r["note"])

    def review_rows(self, include_all: bool = False, handlers: set[str] | None = None) -> list[ReviewRow]:
        sql = self._REVIEW_SQL
        params: list = []
        if not include_all:
            sql += (" AND (t.status IN ('pending', 'failed') OR (t.status='done' AND t.origin IN ('llm', 'memory'))"
                    " OR t.note IS NOT NULL)")
        if handlers:
            sql += f" AND e.handler IN ({','.join('?' * len(handlers))})"
            params.extend(sorted(handlers))
        sql += " ORDER BY e.handler, e.source, e.id"
        return [self._review_row(r) for r in self.conn.execute(sql, params)]

    def get_review_row(self, entry_id: int) -> ReviewRow | None:
        row = self.conn.execute(self._REVIEW_SQL + " AND e.id=?", (entry_id,)).fetchone()
        return self._review_row(row) if row else None

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT t.status, COUNT(*) AS n FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 GROUP BY t.status"
        )
        return {r["status"]: r["n"] for r in rows}

    def origin_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT t.origin, COUNT(*) AS n FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status IN ('done', 'confirmed') GROUP BY t.origin"
        )
        return {r["origin"]: r["n"] for r in rows}

    # ---- runs ----
    def start_run(self, command: str, params: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(command, params, started_at) VALUES(?, ?, ?)",
                (command, json.dumps(params, ensure_ascii=False, default=str), _now()),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, stats: dict) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, stats=? WHERE id=?",
                (_now(), json.dumps(stats, ensure_ascii=False, default=str), run_id),
            )
