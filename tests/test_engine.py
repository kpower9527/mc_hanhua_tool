import time

import pytest

from helpers import FakeClient
from mctrans.config import Settings
from mctrans.models import Entry
from mctrans.store import Workspace
from mctrans.translate.batch import Unit
from mctrans.translate.engine import (
    EngineSettings, RateLimiter, estimate, group_units, make_batches, run_translation,
)
from mctrans.translate.glossary import Glossary


def e(key, text, ns="a"):
    return Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                 locator=key, namespace=ns, original=text, context=key)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path)
    yield w
    w.close()


FAST = EngineSettings(batch_size=2, batch_max_chars=6000, concurrency=2, requests_per_minute=0, max_retries=2)


def test_from_settings():
    s = EngineSettings.from_settings(Settings(batch_size=10, concurrency=3))
    assert s.batch_size == 10 and s.concurrency == 3


def test_group_units_dedupes_and_skips():
    entries = [e("k1", "Gear"), e("k2", "Gear"), e("k3", "§a%s"), e("k4", "Gear", ns="b")]
    for i, x in enumerate(entries, 1):
        x.id = i
    groups, skipped = group_units(entries)
    assert skipped == [3]
    by_ns = {g.namespace: g for g in groups}
    assert [u.entry_ids for u in by_ns["a"].units] == [[1, 2]]
    assert [u.entry_ids for u in by_ns["b"].units] == [[4]]


def test_make_batches_by_count_and_chars():
    us = [Unit("x" * 10, "", [i]) for i in range(5)]
    assert [len(b) for b in make_batches(us, 2, 1000)] == [2, 2, 1]
    assert [len(b) for b in make_batches(us, 10, 25)] == [2, 2, 1]
    big = [Unit("x" * 100, "", [0])]
    assert [len(b) for b in make_batches(big, 10, 25)] == [1]


def test_run_translation_all_success(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Gear"), e("k3", "Shaft"), e("k4", "%s")])
    client = FakeClient()
    progress = []
    report = run_translation(ws, client, Glossary(), FAST, on_progress=progress.append)
    assert (report.translated, report.failed, report.skipped) == (3, 0, 1)
    assert report.requests == 1  # 去重后 2 条，batch_size=2 → 1 次请求
    assert sum(progress) == 3
    texts = {i.entry.locator: (i.text, i.origin) for i in ws.translated_items()}
    assert texts == {"k1": ("译Gear", "llm"), "k2": ("译Gear", "llm"), "k3": ("译Shaft", "llm")}
    assert ws.status_counts() == {"done": 3, "skipped": 1}
    assert ws.pending_entries() == []


def test_run_translation_retries_then_succeeds(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    client = FakeClient(fail_times=1)
    report = run_translation(ws, client, Glossary(), FAST)
    assert report.translated == 2 and report.failed == 0
    assert report.requests >= 2


def test_run_translation_permanent_failure(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Bad %s")])
    client = FakeClient(lambda t: "丢失" if "⟦" in t else "齿轮")
    report = run_translation(ws, client, Glossary(), FAST)
    assert report.translated == 1 and report.failed == 1
    row = ws.conn.execute(
        "SELECT t.status, t.note FROM entries e JOIN translations t ON t.entry_id=e.id WHERE e.locator='k2'"
    ).fetchone()
    assert row["status"] == "failed" and "占位符" in row["note"]
    # 再次运行只处理失败的条目
    client2 = FakeClient()
    report2 = run_translation(ws, client2, Glossary(), FAST)
    assert report2.translated == 1 and len(client2.calls) == 1


def test_rate_limiter_spacing():
    limiter = RateLimiter(per_minute=1200)  # 间隔 0.05 秒
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    assert time.monotonic() - start >= 0.09


def test_rate_limiter_disabled():
    limiter = RateLimiter(per_minute=0)
    start = time.monotonic()
    for _ in range(100):
        limiter.acquire()
    assert time.monotonic() - start < 0.05


def test_estimate():
    entries = [e("k1", "a" * 35), e("k2", "a" * 35), e("k3", "%s")]
    for i, x in enumerate(entries, 1):
        x.id = i
    est = estimate(entries, EngineSettings(batch_size=40), price_input=2.0, price_output=8.0)
    assert (est.entries, est.units, est.requests) == (2, 1, 1)
    assert est.input_tokens > 0 and est.output_tokens > 0
    assert est.cost == round(est.input_tokens / 1e6 * 2.0 + est.output_tokens / 1e6 * 8.0, 4)


from mctrans.translate.engine import select_entries


def test_select_entries():
    entries = [e("k1", "A"), Entry("ftbquests", "s", "q1", "ftbquests", "Quest"), e("k2", "B")]
    assert select_entries(entries, None, None) == entries
    assert [x.locator for x in select_entries(entries, {"ftbquests"}, None)] == ["q1"]
    assert [x.locator for x in select_entries(entries, None, 2)] == ["k1", "q1"]


def test_run_translation_with_explicit_entries(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    subset = ws.pending_entries()[:1]
    report = run_translation(ws, FakeClient(), Glossary(), FAST, entries=subset)
    assert report.translated == 1
    assert [p.locator for p in ws.pending_entries()] == ["k2"]
