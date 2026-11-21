import pytest

from mctrans.applied import pointer_path
from mctrans.handlers.base import OutputSink
from mctrans.handlers.ftbquests import LANG_SOURCE, ZH_FILE, FtbQuestsHandler, read_table
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem
from mctrans.snbt import parse, to_lang_value

LANG = "config/ftbquests/quests/lang"


def put(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def lang_pack(instance_dir):
    put(instance_dir, f"{LANG}/en_us.snbt", '{\n\tfile.0000.title: "My Pack"\n\tquest.01.title: "Old title"\n}\n')
    put(instance_dir, f"{LANG}/en_us/chapters/create.snbt", "\n".join([
        "{",
        '\tquest.01.title: "&6Mechanical Plough"',
        '\tquest.01.quest_desc: ["The &6Plough&r works." "" "Second line"]',
        '\tquest.02.quest_desc: ["A" "B"]',
        '\timage.03.title: ""',
        "}",
    ]))
    put(instance_dir, f"{LANG}/en_us/chapters/old.snbt_merged", '{\n\tquest.99.title: "ignored"\n}\n')
    put(instance_dir, f"{LANG}/zh_cn/chapter.snbt", '{\n\tfile.0000.title: "我的整合包"\n\tquest.02.quest_desc: ["只有一行"]\n}\n')
    return instance_dir


def extract(root):
    h = FtbQuestsHandler()
    inst = load_instance(root)
    assert h.detect(inst)
    return h, list(h.extract(inst))


def test_read_table_merges_split_files(lang_pack):
    warnings = []
    en = read_table(lang_pack, "en_us", warnings)
    assert en == {
        "file.0000.title": "My Pack",
        "quest.01.title": "&6Mechanical Plough",  # 拆分文件优先
        "quest.01.quest_desc": ["The &6Plough&r works.", "", "Second line"],
        "quest.02.quest_desc": ["A", "B"],
    }
    assert warnings == []


def test_extract_lang_entries(lang_pack):
    h, entries = extract(lang_pack)
    got = [(e.locator, e.original, e.existing_zh) for e in entries]
    assert got == [
        ("file.0000.title", "My Pack", "我的整合包"),
        ("quest.01.title", "&6Mechanical Plough", None),
        ("quest.01.quest_desc[0]", "The &6Plough&r works.", None),
        ("quest.01.quest_desc[2]", "Second line", None),
        # quest.02 已有中文但行数不同：保持原样，不提取
    ]
    assert all(e.source == LANG_SOURCE and e.namespace == "ftbquests" for e in entries)
    assert entries[2].context == "quest.01.quest_desc"


def test_detect_requires_quests_dir(instance_dir):
    assert not FtbQuestsHandler().detect(load_instance(instance_dir))


def test_broken_lang_file_warns(lang_pack):
    put(lang_pack, f"{LANG}/en_us/chapters/broken.snbt", '{\n\tquest.x.title: "oops\n}')
    h, entries = extract(lang_pack)
    assert entries and any("broken.snbt" in w for w in h.warnings)


def item(locator, text, origin="llm"):
    entry = Entry("ftbquests", LANG_SOURCE, locator, "ftbquests", "x")
    return TranslatedItem(entry, text, origin, "done")


def test_write_merges_with_existing_chinese(lang_pack):
    sink = OutputSink(lang_pack)
    FtbQuestsHandler().write([
        item("quest.01.title", "&6动力犁"),
        item("quest.01.quest_desc[0]", "这个&6犁&r能用。"),
    ], sink)
    assert list(sink.instance_files) == [ZH_FILE]
    table = {k: to_lang_value(v) for k, v in parse(sink.instance_files[ZH_FILE].decode("utf-8")).items()}
    assert table == {
        "file.0000.title": "我的整合包",
        "quest.01.title": "&6动力犁",
        "quest.01.quest_desc": ["这个&6犁&r能用。", "", "Second line"],
        "quest.02.quest_desc": ["只有一行"],
    }
    assert list(table)[:2] == ["file.0000.title", "quest.01.title"]  # 按英文顺序


def test_rescan_after_apply_reads_original(lang_pack):
    # 模拟 apply 新建了 zh_cn.snbt：扫描时应视为不存在
    put(lang_pack, ZH_FILE, '{\n\tquest.01.title: "&6动力犁"\n}\n')
    run = lang_pack / ".mctrans" / "backups" / "r1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        '{"created": [], "modified": [{"path": "%s", "backup": null, "hash": "x"}], '
        '"options": {"packs": [], "lang_set": false, "lang_before": null}}' % ZH_FILE, encoding="utf-8")
    pointer_path(lang_pack).write_text('{"manifest": ".mctrans/backups/r1/manifest.json"}', encoding="utf-8")
    _, entries = extract(lang_pack)
    assert next(e for e in entries if e.locator == "quest.01.title").existing_zh is None


INLINE_CHAPTER = """{
\tfilename: "create"
\ttitle: "Create"
\tquests: [
\t\t{
\t\t\tid: "01"
\t\t\ttitle: "&6Mechanical Plough"
\t\t\tsubtitle: "{atm9.quest.subtitle}"
\t\t\tdescription: [
\t\t\t\t"The &6Plough&r works."
\t\t\t\t""
\t\t\t\t"Second line"
\t\t\t]
\t\t\ttasks: [{ id: "02", type: "item", item: "create:shaft", title: "Get a Shaft" }]
\t\t\tx: 1.0d
\t\t}
\t]
}
"""


@pytest.fixture
def inline_pack(instance_dir):
    q = "config/ftbquests/quests"
    put(instance_dir, f"{q}/chapters/create.snbt", INLINE_CHAPTER)
    put(instance_dir, f"{q}/chapter_groups.snbt", '{\n\tchapter_groups: [{ id: "G1", title: "Tech" }]\n}\n')
    put(instance_dir, f"{q}/data.snbt", '{\n\ttitle: "My Quests"\n\tversion: 13\n}\n')
    return instance_dir


def test_extract_inline(inline_pack):
    _, entries = extract(inline_pack)
    got = [(e.source.rsplit("/", 1)[1], e.locator, e.original) for e in entries]
    assert got == [
        ("data.snbt", "title", "My Quests"),
        ("chapter_groups.snbt", "chapter_groups[0].title", "Tech"),
        ("create.snbt", "title", "Create"),
        ("create.snbt", "quests[0].title", "&6Mechanical Plough"),
        ("create.snbt", "quests[0].description[0]", "The &6Plough&r works."),
        ("create.snbt", "quests[0].description[2]", "Second line"),
        ("create.snbt", "quests[0].tasks[0].title", "Get a Shaft"),
    ]
    assert entries[3].context == "create.snbt#quests[0].title"


def test_write_inline_replaces_only_text(inline_pack):
    src = "config/ftbquests/quests/chapters/create.snbt"

    def it(locator, text):
        return TranslatedItem(Entry("ftbquests", src, locator, "ftbquests", "x"), text, "llm", "done")

    sink = OutputSink(inline_pack)
    FtbQuestsHandler().write([
        it("quests[0].title", "&6动力犁"),
        it("quests[0].description[2]", "第二行"),
        it("title", "Create"),  # 与原文相同，不替换
    ], sink)
    out = sink.instance_files[src].decode("utf-8")
    original = (inline_pack / src).read_bytes().decode("utf-8")  # Windows 上为 CRLF，需原样保留
    assert out == original.replace('"&6Mechanical Plough"', '"&6动力犁"').replace('"Second line"', '"第二行"')


def test_lang_format_takes_precedence_over_inline(inline_pack):
    put(inline_pack, f"{LANG}/en_us.snbt", '{\n\tquest.01.title: "From lang"\n}\n')
    _, entries = extract(inline_pack)
    assert [e.source for e in entries] == [LANG_SOURCE]
