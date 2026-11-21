from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from ..config import Settings
from ..handlers import get_handler
from ..models import Entry
from ..store import TranslationUpdate, Workspace
from .batch import Unit, UnitResult, translate_batch
from .client import LLMClient
from .glossary import Glossary
from .protect import has_translatable_text, protect

PROMPT_OVERHEAD_TOKENS = 450  # 系统提示词 + 术语表的大致开销
CHARS_PER_TOKEN = 3.5
OUTPUT_RATIO = 1.3


@dataclass
class EngineSettings:
    batch_size: int = 40
    batch_max_chars: int = 6000
    concurrency: int = 4
    requests_per_minute: int = 60
    max_retries: int = 3

    @classmethod
    def from_settings(cls, s: Settings) -> "EngineSettings":
        return cls(s.batch_size, s.batch_max_chars, s.concurrency, s.requests_per_minute, s.max_retries)


@dataclass
class Group:
    handler: str
    namespace: str
    units: list[Unit]


@dataclass
class TranslateReport:
    translated: int = 0
    failed: int = 0
    skipped: int = 0
    requests: int = 0


@dataclass
class Estimate:
    entries: int
    units: int
    requests: int
    input_tokens: int
    output_tokens: int
    cost: float


def group_units(entries: list[Entry]) -> tuple[list[Group], list[int]]:
    groups: dict[tuple[str, str], dict[str, Unit]] = {}
    skipped: list[int] = []
    for e in entries:
        if not has_translatable_text(protect(e.original).text):
            skipped.append(e.id)
            continue
        units = groups.setdefault((e.handler, e.namespace), {})
        unit = units.get(e.original)
        if unit is None:
            units[e.original] = Unit(e.original, e.context, [e.id])
        else:
            unit.entry_ids.append(e.id)
    return [Group(h, ns, list(us.values())) for (h, ns), us in groups.items()], skipped


def select_entries(entries: list[Entry], only: set[str] | None, limit: int | None) -> list[Entry]:
    """按处理器筛选并限制条目数（用于抽样翻译、控制费用）。"""
    chosen = [e for e in entries if not only or e.handler in only]
    return chosen[:limit] if limit else chosen


def make_batches(units: list[Unit], batch_size: int, max_chars: int) -> list[list[Unit]]:
    batches: list[list[Unit]] = []
    current: list[Unit] = []
    size = 0
    for u in units:
        n = len(u.original) + len(u.hint)
        if current and (len(current) >= batch_size or size + n > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(u)
        size += n
    if current:
        batches.append(current)
    return batches


class RateLimiter:
    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            time.sleep(wait)


def run_translation(ws: Workspace, client: LLMClient, glossary: Glossary, settings: EngineSettings,
                    on_progress: Callable[[int], None] | None = None,
                    entries: list[Entry] | None = None) -> TranslateReport:
    report = TranslateReport()
    groups, skipped = group_units(ws.pending_entries() if entries is None else entries)
    if skipped:
        ws.save_translations([TranslationUpdate(i, None, None, "skipped") for i in skipped])
        report.skipped = len(skipped)

    limiter = RateLimiter(settings.requests_per_minute)
    kinds = {g.handler: get_handler(g.handler).kind_label for g in groups}
    last_error: dict[int, str] = {}
    batch_size = settings.batch_size
    pending = [(g, b) for g in groups for b in make_batches(g.units, batch_size, settings.batch_max_chars)]

    def job(group: Group, batch: list[Unit]) -> list[UnitResult]:
        limiter.acquire()
        return translate_batch(client, batch, group.namespace, kinds[group.handler], glossary)

    for _attempt in range(settings.max_retries + 1):
        if not pending:
            break
        failed: dict[int, tuple[Group, list[Unit]]] = {}
        with ThreadPoolExecutor(max_workers=max(1, settings.concurrency)) as pool:
            futures = {pool.submit(job, g, b): (g, b) for g, b in pending}
            for fut in as_completed(futures):
                group, batch = futures[fut]
                report.requests += 1
                updates: list[TranslationUpdate] = []
                done = 0
                for unit, result in zip(batch, fut.result()):
                    if result.text is not None:
                        updates.extend(
                            TranslationUpdate(i, result.text, "llm", "done", client.model, result.note)
                            for i in unit.entry_ids
                        )
                        done += len(unit.entry_ids)
                    else:
                        last_error[id(unit)] = result.error or "未知错误"
                        failed.setdefault(id(group), (group, []))[1].append(unit)
                if updates:
                    ws.save_translations(updates)
                report.translated += done
                if on_progress and done:
                    on_progress(done)
        batch_size = max(1, batch_size // 4)
        pending = [
            (g, b) for g, us in failed.values()
            for b in make_batches(us, batch_size, settings.batch_max_chars)
        ]

    final: list[TranslationUpdate] = []
    for _group, batch in pending:
        for unit in batch:
            final.extend(
                TranslationUpdate(i, None, None, "failed", None, last_error.get(id(unit)))
                for i in unit.entry_ids
            )
            report.failed += len(unit.entry_ids)
    if final:
        ws.save_translations(final)
    return report


def estimate(entries: list[Entry], settings: EngineSettings, price_input: float,
             price_output: float) -> Estimate:
    groups, skipped = group_units(entries)
    units = [u for g in groups for u in g.units]
    requests = sum(len(make_batches(g.units, settings.batch_size, settings.batch_max_chars)) for g in groups)
    in_chars = sum(len(u.original) + len(u.hint) for u in units)
    out_chars = sum(len(u.original) for u in units)
    input_tokens = int(in_chars / CHARS_PER_TOKEN) + requests * PROMPT_OVERHEAD_TOKENS
    output_tokens = int(out_chars / CHARS_PER_TOKEN * OUTPUT_RATIO) + requests * 10
    cost = round(input_tokens / 1e6 * price_input + output_tokens / 1e6 * price_output, 4)
    return Estimate(len(entries) - len(skipped), len(units), requests, input_tokens, output_tokens, cost)
