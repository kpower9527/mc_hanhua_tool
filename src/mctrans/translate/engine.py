from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
    cancelled: bool = False


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
                    entries: list[Entry] | None = None,
                    should_stop: Callable[[], bool] | None = None,
                    on_error: Callable[[int, str, bool], None] | None = None) -> TranslateReport:
    """翻译待处理条目。

    失败的条目立即拆成更小的批次放回队列重试（不等其他批次），重试 max_retries 次后标记为失败。
    on_progress(n)：n 条已处理完（成功或最终失败）；on_error(条数, 原因, 是否放弃)：有条目失败时回调。
    """
    report = TranslateReport()
    groups, skipped = group_units(ws.pending_entries() if entries is None else entries)
    if skipped:
        ws.save_translations([TranslationUpdate(i, None, None, "skipped") for i in skipped])
        report.skipped = len(skipped)

    limiter = RateLimiter(settings.requests_per_minute)
    kinds = {g.handler: get_handler(g.handler).kind_label for g in groups}

    def job(group: Group, batch: list[Unit], lenient: bool) -> list[UnitResult]:
        limiter.acquire()
        return translate_batch(client, batch, group.namespace, kinds[group.handler], glossary, lenient)

    workers = max(1, settings.concurrency)
    queue: deque[tuple[Group, list[Unit], int]] = deque(
        (g, b, 0) for g in groups for b in make_batches(g.units, settings.batch_size, settings.batch_max_chars)
    )
    in_flight: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def submit_more() -> None:
            # 滑动窗口：同时在途的批次不超过并发数，取消后不再提交新批次
            while queue and len(in_flight) < workers and not report.cancelled:
                g, b, attempt = queue.popleft()
                # 最后一次尝试时放宽：只少颜色码的译文也接受（带审校备注），避免长段落整条失败
                lenient = attempt >= settings.max_retries
                in_flight[pool.submit(job, g, b, lenient)] = (g, b, attempt)

        submit_more()
        while in_flight:
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in finished:
                group, batch, attempt = in_flight.pop(fut)
                report.requests += 1
                updates: list[TranslationUpdate] = []
                done = 0
                retry: list[Unit] = []
                errors: dict[str, int] = {}
                for unit, result in zip(batch, fut.result()):
                    if result.text is not None:
                        updates.extend(
                            TranslationUpdate(i, result.text, "llm", "done", client.model, result.note)
                            for i in unit.entry_ids
                        )
                        done += len(unit.entry_ids)
                        continue
                    error = result.error or "未知错误"
                    errors[error] = errors.get(error, 0) + 1
                    if attempt < settings.max_retries:
                        retry.append(unit)
                    else:
                        updates.extend(TranslationUpdate(i, None, None, "failed", None, error) for i in unit.entry_ids)
                        report.failed += len(unit.entry_ids)
                        done += len(unit.entry_ids)
                if updates:
                    ws.save_translations(updates)
                report.translated += sum(1 for u in updates if u.status == "done")
                if on_error and errors:
                    reason = max(errors, key=errors.get)
                    on_error(sum(errors.values()), reason, not retry)
                if retry:
                    size = max(1, settings.batch_size // (4 ** (attempt + 1)))
                    for b in make_batches(retry, size, settings.batch_max_chars):
                        queue.append((group, b, attempt + 1))
                if on_progress and done:
                    on_progress(done)
            if should_stop is not None and should_stop():
                report.cancelled = True  # 已完成的已保存，其余条目保持待翻译
            submit_more()
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
