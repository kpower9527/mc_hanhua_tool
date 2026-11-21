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
