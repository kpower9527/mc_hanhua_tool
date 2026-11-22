import json

import pytest

from helpers import jar_bytes, lang, make_jar
from mctrans.handlers import all_handlers, get_handler
from mctrans.handlers.base import OutputSink
from mctrans.handlers.lang_json import LangJsonHandler, parse_lang
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem


def extract(root):
    h = LangJsonHandler()
    return h, list(h.extract(load_instance(root)))


def test_registry():
    assert [h.name for h in all_handlers()] == [
        "lang_json", "ftbquests", "patchouli", "betterquesting", "kubejs", "crafttweaker", "structures", "config_text"]
    assert isinstance(get_handler("lang_json"), LangJsonHandler)
    with pytest.raises(KeyError):
        get_handler("nope")


def test_parse_lang_lenient():
    data = '﻿{\n  // comment\n  "a": "A",\n  "b": 1,\n  "c": "C",\n}'.encode("utf-8")
    assert parse_lang(data) == {"a": "A", "c": "C"}


def test_parse_lang_rejects_non_object():
    with pytest.raises(ValueError):
        parse_lang(b"[1, 2]")


def test_extract_from_jar_with_builtin_zh(instance_dir):
    make_jar(instance_dir / "mods" / "create.jar", {
        "assets/create/lang/en_us.json": lang({"item.create.gear": "Gear", "item.create.shaft": "Shaft", "blank": " "}),
        "assets/create/lang/zh_cn.json": lang({"item.create.gear": "齿轮"}),
        "assets/create/lang/de_de.json": lang({"item.create.gear": "Zahnrad"}),
    })
    h, entries = extract(instance_dir)
    assert [(x.locator, x.original, x.existing_zh) for x in entries] == [
        ("item.create.gear", "Gear", "齿轮"),
        ("item.create.shaft", "Shaft", None),
    ]
    first = entries[0]
    assert first.handler == "lang_json"
    assert first.source == "jar:assets/create/lang/en_us.json"
    assert first.namespace == "create"
    assert first.context == "item.create.gear"
    assert len(first.file_hash) == 40
    assert h.warnings == []


def test_extract_case_insensitive_filenames(instance_dir):
    make_jar(instance_dir / "mods" / "old.jar", {
        "assets/old/lang/en_US.json": lang({"k": "Hello"}),
        "assets/old/lang/zh_CN.json": lang({"k": "你好"}),
    })
    _, entries = extract(instance_dir)
    assert [(x.locator, x.existing_zh) for x in entries] == [("k", "你好")]


def test_extract_nested_jar(instance_dir):
    inner = jar_bytes({"assets/lib/lang/en_us.json": lang({"lib.k": "Library"})})
    make_jar(instance_dir / "mods" / "outer.jar", {
        "assets/outer/lang/en_us.json": lang({"outer.k": "Outer"}),
        "META-INF/jars/lib.jar": inner,
    })
    _, entries = extract(instance_dir)
    assert sorted(x.namespace for x in entries) == ["lib", "outer"]


def test_extract_dedupes_same_key_across_jars(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"assets/shared/lang/en_us.json": lang({"k": "One"})})
    make_jar(instance_dir / "mods" / "b.jar", {"assets/shared/lang/en_us.json": lang({"k": "Two"})})
    _, entries = extract(instance_dir)
    assert [(x.locator, x.original) for x in entries] == [("k", "One")]


def test_extract_kubejs_assets(instance_dir):
    d = instance_dir / "kubejs" / "assets" / "kubejs" / "lang"
    d.mkdir(parents=True)
    (d / "en_us.json").write_text(lang({"item.kubejs.dust": "Magic Dust"}), encoding="utf-8")
    (d / "zh_cn.json").write_text(lang({"item.kubejs.dust": "魔法粉"}), encoding="utf-8")
    _, entries = extract(instance_dir)
    assert [(x.source, x.locator, x.existing_zh) for x in entries] == [
        ("kubejs/assets/kubejs/lang/en_us.json", "item.kubejs.dust", "魔法粉")
    ]


def test_extract_broken_files_warn(instance_dir):
    make_jar(instance_dir / "mods" / "bad.jar", {"assets/bad/lang/en_us.json": "{not json"})
    (instance_dir / "mods" / "corrupt.jar").write_bytes(b"garbage")
    h, entries = extract(instance_dir)
    assert entries == []
    assert any("bad.jar" in w for w in h.warnings)
    assert any("corrupt.jar" in w for w in h.warnings)


def item(ns, key, text):
    entry = Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                  locator=key, namespace=ns, original="x")
    return TranslatedItem(entry=entry, text=text, origin="llm", status="done")


def test_write_groups_by_namespace():
    sink = OutputSink()
    LangJsonHandler().write([item("a", "k1", "一"), item("b", "k2", "二"), item("a", "k3", "三")], sink)
    assert sorted(sink.pack_files) == ["assets/a/lang/zh_cn.json", "assets/b/lang/zh_cn.json"]
    assert json.loads(sink.pack_files["assets/a/lang/zh_cn.json"].decode("utf-8")) == {"k1": "一", "k3": "三"}
    assert "一" in sink.pack_files["assets/a/lang/zh_cn.json"].decode("utf-8")  # 不转义中文
    assert sink.instance_files == {}


from mctrans.handlers.lang_json import dump_lang_legacy, parse_lang_legacy


def test_extract_resourceloader_dir(tmp_path):
    root = tmp_path / "rl"
    (root / "mods").mkdir(parents=True)
    make_jar(root / "mods" / "bq.jar", {"assets/betterquesting/lang/en_us.lang": "betterquesting.quest.name.1=Jar text\n"})
    d = root / "resources" / "betterquesting" / "lang"
    d.mkdir(parents=True)
    (d / "en_us.lang").write_bytes(b"betterquesting.quest.name.1=Welcome to RLCraft\nother.key=Hello\n")
    (d / "zh_cn.lang").write_bytes("other.key=你好\n".encode("utf-8"))
    entries = list(LangJsonHandler().extract(load_instance(root, mc_version="1.12.2")))
    got = [(x.source, x.locator, x.original, x.existing_zh) for x in entries]
    assert got == [
        ("jar:assets/betterquesting/lang/en_us.lang", "betterquesting.quest.name.1", "Jar text", None),
        ("resources/betterquesting/lang/en_us.lang", "betterquesting.quest.name.1", "Welcome to RLCraft", None),
        ("resources/betterquesting/lang/en_us.lang", "other.key", "Hello", "你好"),
    ]
    sink = OutputSink()
    LangJsonHandler().write([TranslatedItem(entries[0], "罐内文本", "llm", "done"),
                             TranslatedItem(entries[1], "欢迎来到 RLCraft", "llm", "done")], sink)
    # 同一个键：resources/ 中的版本（游戏实际显示的英文）优先
    assert sink.pack_files["assets/betterquesting/lang/zh_cn.lang"] == "betterquesting.quest.name.1=欢迎来到 RLCraft\n".encode("utf-8")


def test_parse_lang_legacy():
    data = "\ufeff#PARSE_ESCAPES\n# comment\nitem.a.name=Gear\n\ntile.b.name=Shaft = long\nbad line\ntip.c=Line1\\nLine2\n".encode("utf-8")
    assert parse_lang_legacy(data) == {"item.a.name": "Gear", "tile.b.name": "Shaft = long", "tip.c": "Line1\\nLine2"}


def test_dump_lang_legacy():
    assert dump_lang_legacy({"a": "齿轮", "b": "轴"}) == "a=齿轮\nb=轴\n".encode("utf-8")
    assert dump_lang_legacy({"a": "第一行\\n第二行"}).startswith(b"#PARSE_ESCAPES\n")


def test_extract_and_write_legacy_lang(tmp_path):
    root = tmp_path / "old"
    (root / "mods").mkdir(parents=True)
    make_jar(root / "mods" / "old.jar", {
        "assets/oldmod/lang/en_US.lang": "item.gear.name=Gear\ntip.x=Hold %s\n",
        "assets/oldmod/lang/zh_CN.lang": "item.gear.name=齿轮\n",
        "assets/both/lang/en_us.lang": "k=From lang\n",
        "assets/both/lang/en_us.json": lang({"k": "From json"}),
    })
    inst = load_instance(root, mc_version="1.12.2")
    entries = list(LangJsonHandler().extract(inst))
    got = sorted((x.source, x.locator, x.original, x.existing_zh) for x in entries)
    assert got == [
        ("jar:assets/both/lang/en_us.json", "k", "From json", None),
        ("jar:assets/oldmod/lang/en_us.lang", "item.gear.name", "Gear", "齿轮"),
        ("jar:assets/oldmod/lang/en_us.lang", "tip.x", "Hold %s", None),
    ]
    sink = OutputSink()
    legacy = next(x for x in entries if x.locator == "tip.x")
    LangJsonHandler().write([TranslatedItem(legacy, "按住 %s", "llm", "done")], sink)
    assert sink.pack_files == {"assets/oldmod/lang/zh_cn.lang": "tip.x=按住 %s\n".encode("utf-8")}
