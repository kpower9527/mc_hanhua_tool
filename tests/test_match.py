import pytest

from helpers import jar_bytes, lang
from mctrans.cfpa.index import CfpaIndex
from mctrans.match import remember_translations, run_matching
from mctrans.memory import Memory, MemoryRecord
from mctrans.models import Entry
from mctrans.store import TranslationUpdate, Workspace


def e(key, text, zh=None, ns="create"):
    return Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                 locator=key, namespace=ns, original=text, context=key, existing_zh=zh)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path / "inst")
    yield w
    w.close()


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "memory.db")
    yield m
    m.close()


def cfpa(mapping):
    return CfpaIndex.from_zips([jar_bytes({"assets/create/lang/zh_cn.json": lang(mapping)})])


def texts(ws):
    return {i.entry.locator: (i.text, i.origin) for i in ws.translated_items()}


def test_priority_chain(ws, memory):
    ws.sync_entries("lang_json", [
        e("k.builtin", "Gear", zh="齿轮"),
        e("k.cfpa", "Shaft"),
        e("k.stale", "%s items"),
        e("k.mem", "Belt"),
        e("k.human", "Casing"),
        e("k.none", "Cog"),
    ])
    idx = cfpa({"k.builtin": "CFPA齿轮", "k.cfpa": "传动杆", "k.stale": "物品", "k.human": "CFPA机壳"})
    memory.remember([
        MemoryRecord("create", "Belt", "传送带", "llm"),
        MemoryRecord("create", "Casing", "人工机壳", "human", confirmed=True),
    ])
    stats = run_matching(ws, idx, memory)
    assert (stats.builtin, stats.cfpa, stats.cfpa_rejected, stats.human, stats.memory) == (1, 2, 1, 1, 1)
    assert texts(ws) == {
        "k.builtin": ("齿轮", "mod_builtin"),
        "k.cfpa": ("传动杆", "cfpa"),
        "k.mem": ("传送带", "memory"),
        "k.human": ("人工机壳", "human"),
    }
    assert [p.locator for p in ws.pending_entries()] == ["k.stale", "k.none"]


def test_cfpa_overrides_llm(ws):
    ws.sync_entries("lang_json", [e("k1", "Shaft")])
    (eid,) = [p.id for p in ws.pending_entries()]
    ws.save_translations([TranslationUpdate(eid, "机翻轴", "llm", "done", "m")])
    run_matching(ws, cfpa({"k1": "传动杆"}), None)
    assert texts(ws) == {"k1": ("传动杆", "cfpa")}


def test_cfpa_ignores_identical_english(ws):
    ws.sync_entries("lang_json", [e("k1", "Create")])
    assert run_matching(ws, cfpa({"k1": "Create"}), None).cfpa == 0


def test_without_sources_only_builtin(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", zh="齿轮"), e("k2", "Shaft")])
    stats = run_matching(ws, None, None)
    assert (stats.builtin, stats.cfpa, stats.memory) == (1, 0, 0)


def test_remember_translations(ws, memory):
    ws.sync_entries("lang_json", [e("k1", "Gear", zh="齿轮"), e("k2", "Shaft"), e("k3", "Belt")])
    ws.apply_builtin()
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k2"], "轴", "llm", "done", "qwen"),
        TranslationUpdate(ids["k3"], "传送带", "human", "confirmed"),
    ])
    assert remember_translations(ws, memory) == 2
    assert memory.lookup("create", "Shaft").model == "qwen"
    assert memory.lookup("create", "Belt").confirmed is True
    assert memory.lookup("create", "Gear") is None  # mod 自带的不入库
