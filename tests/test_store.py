import pytest

from mctrans.models import Entry
from mctrans.store import TranslationUpdate, Workspace


def e(locator, original, existing_zh=None, source="jar:assets/a/lang/en_us.json"):
    return Entry(handler="lang_json", source=source, locator=locator, namespace="a",
                 original=original, context=locator, existing_zh=existing_zh)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path)
    yield w
    w.close()


def test_for_instance_path(tmp_path):
    with Workspace.for_instance(tmp_path) as w:
        assert w.path == tmp_path / ".mctrans" / "workspace.db"
    assert w.path.exists()


def test_meta(ws):
    assert ws.get_meta("mc_version") is None
    ws.set_meta("mc_version", "1.20.1")
    ws.set_meta("mc_version", "1.20.4")
    assert ws.get_meta("mc_version") == "1.20.4"


def test_sync_add_and_unchanged(ws):
    s = ws.sync_entries("lang_json", [e("item.a.x", "Gear"), e("item.a.y", "Shaft")])
    assert (s.added, s.changed, s.unchanged, s.stale) == (2, 0, 0, 0)
    s = ws.sync_entries("lang_json", [e("item.a.x", "Gear"), e("item.a.y", "Shaft")])
    assert (s.added, s.changed, s.unchanged, s.stale) == (0, 0, 2, 0)
    pending = ws.pending_entries()
    assert [p.locator for p in pending] == ["item.a.x", "item.a.y"]
    assert all(p.id is not None for p in pending)


def test_sync_changed_resets_and_flags_confirmed(ws):
    ws.sync_entries("lang_json", [e("k1", "Old"), e("k2", "Old2")])
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k1"], "旧", "human", "confirmed"),
        TranslationUpdate(ids["k2"], "旧2", "llm", "done", "m"),
    ])
    s = ws.sync_entries("lang_json", [e("k1", "New"), e("k2", "New2")])
    assert s.changed == 2
    rows = {r["locator"]: r for r in ws.conn.execute(
        "SELECT e.locator, t.status, t.note FROM entries e JOIN translations t ON t.entry_id=e.id")}
    assert rows["k1"]["status"] == "pending" and rows["k1"]["note"] == "原文已变更，需复核"
    assert rows["k2"]["status"] == "pending" and rows["k2"]["note"] is None


def test_sync_marks_stale_and_revives(ws):
    ws.sync_entries("lang_json", [e("k1", "A"), e("k2", "B")])
    s = ws.sync_entries("lang_json", [e("k1", "A")])
    assert s.stale == 1
    assert [p.locator for p in ws.pending_entries()] == ["k1"]
    ws.sync_entries("lang_json", [e("k1", "A"), e("k2", "B")])
    assert len(ws.pending_entries()) == 2


def test_sync_stale_scoped_to_handler(ws):
    ws.sync_entries("lang_json", [e("k1", "A")])
    ws.sync_entries("other", [])
    assert len(ws.pending_entries()) == 1


def test_apply_builtin(ws):
    ws.sync_entries("lang_json", [
        e("k1", "Gear", existing_zh="齿轮"),
        e("k2", "Shaft", existing_zh="Shaft"),  # 自带 zh_cn 只是照抄英文
        e("k3", "Belt"),
    ])
    assert ws.apply_builtin() == 1
    assert ws.apply_builtin() == 0  # 幂等
    items = ws.translated_items()
    assert [(i.entry.locator, i.text, i.origin) for i in items] == [("k1", "齿轮", "mod_builtin")]
    assert [p.locator for p in ws.pending_entries()] == ["k2", "k3"]


def test_apply_builtin_overrides_llm_but_not_human(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k1"], "机翻", "llm", "done", "m"),
        TranslationUpdate(ids["k2"], "人工", "human", "confirmed"),
    ])
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft", "传动杆")])
    assert ws.apply_builtin() == 1
    texts = {i.entry.locator: i.text for i in ws.translated_items()}
    assert texts == {"k1": "齿轮", "k2": "人工"}


def test_save_translations_protects_confirmed(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear")])
    (eid,) = [p.id for p in ws.pending_entries()]
    assert ws.save_translations([TranslationUpdate(eid, "齿轮", "human", "confirmed")]) == 1
    assert ws.save_translations([TranslationUpdate(eid, "机翻", "llm", "done", "m")]) == 0
    assert ws.translated_items()[0].text == "齿轮"


def test_pending_includes_failed(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear")])
    (eid,) = [p.id for p in ws.pending_entries()]
    ws.save_translations([TranslationUpdate(eid, None, None, "failed", note="x")])
    assert [p.id for p in ws.pending_entries()] == [eid]


def test_translated_items_filter_by_handler(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮")])
    ws.apply_builtin()
    assert len(ws.translated_items("lang_json")) == 1
    assert ws.translated_items("ftbquests") == []


def test_glossary_pairs(ws):
    ws.sync_entries("lang_json", [
        e("item.a.gear", "Gear", "齿轮"),
        e("block.a.casing", "Andesite Casing", "安山机壳"),
        e("tooltip.a.hint", "Right click", "右键"),
    ])
    ws.apply_builtin()
    assert sorted(ws.glossary_pairs()) == [("Andesite Casing", "安山机壳"), ("Gear", "齿轮")]


def test_status_counts(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft")])
    ws.apply_builtin()
    assert ws.status_counts() == {"done": 1, "pending": 1}


def test_runs(ws):
    run_id = ws.start_run("scan", {"a": 1})
    ws.finish_run(run_id, {"added": 2})
    row = ws.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["command"] == "scan" and row["finished_at"] is not None


def test_fill_from_pending_and_overridable(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft"), e("k3", "Belt", "传送带")])
    ws.apply_builtin()
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([TranslationUpdate(ids["k2"], "机翻", "llm", "done", "m")])
    table = {"Gear": "齿轮", "Shaft": "传动杆", "Belt": "皮带"}
    n = ws.fill_from("cfpa", lambda en: table.get(en.original), frozenset({"llm", "memory"}))
    assert n == 2  # k1 待翻译、k2 机翻可覆盖；k3 是 mod 自带，不可覆盖
    texts = {i.entry.locator: (i.text, i.origin) for i in ws.translated_items()}
    assert texts == {"k1": ("齿轮", "cfpa"), "k2": ("传动杆", "cfpa"), "k3": ("传送带", "mod_builtin")}
    assert ws.fill_from("cfpa", lambda en: table.get(en.original), frozenset({"llm"})) == 0  # 幂等


def test_fill_from_skips_confirmed_and_handler_filter(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear")])
    ws.sync_entries("other", [Entry("other", "f", "k9", "a", "Gear")])
    (eid,) = [p.id for p in ws.pending_entries() if p.locator == "k1"]
    ws.save_translations([TranslationUpdate(eid, "人工齿轮", "human", "confirmed")])
    assert ws.fill_from("cfpa", lambda en: "齿轮", frozenset({"human"}), handler="lang_json") == 0
    assert ws.fill_from("memory", lambda en: "齿轮", handler="other") == 1


def test_origin_counts(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft")])
    ws.apply_builtin()
    assert ws.origin_counts() == {"mod_builtin": 1}
