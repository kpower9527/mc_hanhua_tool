from __future__ import annotations

from dataclasses import dataclass

from .cfpa.index import CfpaIndex, compatible
from .memory import Memory, MemoryRecord
from .models import Entry
from .store import Workspace

NON_HUMAN = frozenset({"mod_builtin", "cfpa", "memory", "llm"})


@dataclass
class MatchStats:
    builtin: int = 0
    cfpa: int = 0
    cfpa_rejected: int = 0
    human: int = 0
    memory: int = 0


def run_matching(ws: Workspace, cfpa: CfpaIndex | None, memory: Memory | None) -> MatchStats:
    stats = MatchStats()
    stats.builtin = ws.apply_builtin()

    if cfpa is not None:
        rejected = 0

        def from_cfpa(e: Entry) -> str | None:
            nonlocal rejected
            zh = cfpa.lookup(e.namespace, e.locator)
            if not zh or zh == e.original:
                return None
            if not compatible(e.original, zh):
                rejected += 1
                return None
            return zh

        stats.cfpa = ws.fill_from("cfpa", from_cfpa, frozenset({"llm", "memory"}), handler="lang_json")
        stats.cfpa_rejected = rejected

    if memory is not None:
        def confirmed(e: Entry) -> str | None:
            r = memory.lookup(e.namespace, e.original)
            return r.text if r is not None and r.confirmed else None

        def unconfirmed(e: Entry) -> str | None:
            r = memory.lookup(e.namespace, e.original)
            return r.text if r is not None and not r.confirmed else None

        stats.human = ws.fill_from("human", confirmed, NON_HUMAN)
        stats.memory = ws.fill_from("memory", unconfirmed)
    return stats


def remember_translations(ws: Workspace, memory: Memory) -> int:
    """把工作区中机翻和人工译文写入全局记忆库（人工译文记为已确认）。"""
    rows = ws.conn.execute(
        "SELECT e.namespace, e.original, t.text, t.origin, t.model "
        "FROM entries e JOIN translations t ON t.entry_id=e.id "
        "WHERE e.stale=0 AND t.status IN ('done', 'confirmed') AND t.text IS NOT NULL "
        "AND t.origin IN ('llm', 'human')"
    )
    return memory.remember([
        MemoryRecord(r["namespace"], r["original"], r["text"], r["origin"], r["origin"] == "human", r["model"])
        for r in rows
    ])
