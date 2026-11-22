from __future__ import annotations

from dataclasses import dataclass

from .cfpa.index import CfpaIndex, compatible
from .memory import Memory, MemoryRecord, memory_key
from .models import Entry
from .store import Workspace

NON_HUMAN = frozenset({"mod_builtin", "cfpa", "respack", "memory", "llm"})


@dataclass
class MatchStats:
    builtin: int = 0
    cfpa: int = 0
    cfpa_rejected: int = 0
    respack: int = 0
    human: int = 0
    memory: int = 0


def run_matching(ws: Workspace, cfpa: CfpaIndex | None, memory: Memory | None,
                 patches=None) -> MatchStats:
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

    if patches is not None and len(patches):
        # 整合包里已安装的汉化补丁：专为该整合包制作的人工译文，优先于 mod 自带、CFPA 和机翻
        def from_patch(e: Entry) -> str | None:
            zh = patches.lookup(e.namespace, e.locator)
            if not zh or zh == e.original or not compatible(e.original, zh):
                return None
            return zh

        stats.respack = ws.fill_from("respack", from_patch, frozenset({"mod_builtin", "cfpa", "llm", "memory"}),
                                     handler="lang_json")

    if memory is not None:
        # 一次性读入内存再匹配：逐条查询在大整合包上要十几秒
        snapshot = memory.snapshot()
        confirmed = {k: text for k, (text, ok) in snapshot.items() if ok}
        unconfirmed = {k: text for k, (text, ok) in snapshot.items() if not ok}
        if confirmed:
            stats.human = ws.fill_from("human", lambda e: confirmed.get(memory_key(e.namespace, e.original)),
                                       NON_HUMAN)
        if unconfirmed:
            stats.memory = ws.fill_from("memory", lambda e: unconfirmed.get(memory_key(e.namespace, e.original)))
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
