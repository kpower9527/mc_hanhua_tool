import json

from helpers import make_jar
from mctrans.handlers.base import OutputSink
from mctrans.handlers.patchouli import PatchouliHandler, set_field, text_fields
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem

BOOK = "assets/ars/patchouli_books/notebook"
ENTRY = {
    "name": "Spell Basics",
    "icon": "ars:spell_book",
    "category": "ars:spells",
    "pages": [
        {"type": "patchouli:text", "text": "Spells are $(item)powerful$(). Use them wisely."},
        {"type": "patchouli:spotlight", "item": "ars:wand", "title": "The Wand", "text": "A wand."},
        {"type": "patchouli:link", "url": "https://x", "link_text": "Wiki", "text": "book.ars.key"},
        {"type": "patchouli:entity", "entity": "minecraft:cow", "name": "Cow"},
    ],
}
CATEGORY = {"name": "Spells", "description": "All about spells.", "icon": "ars:spell_book"}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_text_fields():
    assert text_fields(ENTRY) == [
        ("name", "Spell Basics"),
        ("pages[0].text", "Spells are $(item)powerful$(). Use them wisely."),
        ("pages[1].title", "The Wand"),
        ("pages[1].text", "A wand."),
        ("pages[2].link_text", "Wiki"),
        ("pages[3].name", "Cow"),
    ]
    assert text_fields({"name": "book.ars.name"}) == []


def test_set_field():
    obj = json.loads(dumps(ENTRY))
    set_field(obj, "pages[1].title", "法杖")
    set_field(obj, "name", "法术基础")
    assert obj["pages"][1]["title"] == "法杖" and obj["name"] == "法术基础"


def make_book(instance_dir):
    return make_jar(instance_dir / "mods" / "ars-1.0.jar", {
        f"{BOOK}/en_us/entries/basics/spells.json": dumps(ENTRY),
        f"{BOOK}/en_us/categories/spells.json": dumps(CATEGORY),
        f"{BOOK}/en_us/entries/done.json": dumps({"name": "Done", "pages": []}),
        f"{BOOK}/zh_cn/entries/done.json": dumps({"name": "完成", "pages": []}),
        "data/ars/patchouli_books/old/en_us/entries/a.json": dumps({"name": "Old"}),
    })


def test_extract(instance_dir):
    make_book(instance_dir)
    h = PatchouliHandler()
    entries = list(h.extract(load_instance(instance_dir)))
    sources = {e.source for e in entries}
    assert sources == {
        f"mods/ars-1.0.jar!{BOOK}/en_us/entries/basics/spells.json",
        f"mods/ars-1.0.jar!{BOOK}/en_us/categories/spells.json",
    }  # done.json 已有中文，跳过
    spell = [e for e in entries if e.source.endswith("spells.json") and "entries" in e.source]
    assert [e.locator for e in spell] == ["name", "pages[0].text", "pages[1].title", "pages[1].text",
                                          "pages[2].link_text", "pages[3].name"]
    assert spell[0].namespace == "ars" and spell[0].context == "notebook/entries/basics/spells.json#name"
    assert len(h.warnings) == 1 and "data" in h.warnings[0]


def test_extract_kubejs_assets(instance_dir):
    d = instance_dir / "kubejs" / "assets" / "kubejs" / "patchouli_books" / "guide" / "en_us" / "entries"
    d.mkdir(parents=True)
    (d / "intro.json").write_text(dumps({"name": "Intro", "pages": [{"type": "patchouli:text", "text": "Hi there"}]}),
                                  encoding="utf-8")
    entries = list(PatchouliHandler().extract(load_instance(instance_dir)))
    assert [(e.source, e.locator) for e in entries] == [
        ("kubejs/assets/kubejs/patchouli_books/guide/en_us/entries/intro.json", "name"),
        ("kubejs/assets/kubejs/patchouli_books/guide/en_us/entries/intro.json", "pages[0].text"),
    ]


def test_write(instance_dir):
    make_book(instance_dir)
    src = f"mods/ars-1.0.jar!{BOOK}/en_us/entries/basics/spells.json"

    def it(locator, text):
        return TranslatedItem(Entry("patchouli", src, locator, "ars", "x"), text, "llm", "done")

    sink = OutputSink(instance_dir)
    PatchouliHandler().write([it("name", "法术基础"), it("pages[1].title", "法杖")], sink)
    out_path = f"{BOOK}/zh_cn/entries/basics/spells.json"
    assert list(sink.pack_files) == [out_path]
    out = json.loads(sink.pack_files[out_path].decode("utf-8"))
    assert out["name"] == "法术基础" and out["pages"][1]["title"] == "法杖"
    assert out["pages"][0]["text"] == ENTRY["pages"][0]["text"]  # 未翻译的保持英文
    assert out["pages"][1]["item"] == "ars:wand"
