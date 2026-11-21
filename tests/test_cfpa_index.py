import pytest

from helpers import jar_bytes, lang
from mctrans.cfpa.index import CfpaIndex, compatible


@pytest.mark.parametrize("original, translated, ok", [
    ("%s killed %s", "%2$s 被 %1$s 杀死", True),
    ("Speed: %.2f m/s", "速度：%.2f 米/秒", True),
    ("Hello {0}", "你好 {0}", True),
    ("100%% done", "已完成", True),
    ("%s items", "物品", False),
    ("%d items", "%s 个物品", False),
    ("Hi {player}", "你好 {name}", False),
    ("No args", "没有参数", True),
])
def test_compatible(original, translated, ok):
    assert compatible(original, translated) is ok


def test_index_lookup_and_priority():
    primary = jar_bytes({
        "pack.mcmeta": "{}",
        "assets/create/lang/zh_cn.json": lang({"item.create.shaft": "传动杆"}),
        "assets/create/lang/zh_cn.flatten.json5": "{}",
        "assets/lang/zh_cn.json": lang({"x": "y"}),  # 没有命名空间，忽略
    })
    secondary = jar_bytes({
        "assets/create/lang/zh_cn.json": lang({"item.create.shaft": "轴", "item.create.gear": "齿轮"}),
        "assets/other/lang/zh_cn.json": lang({"k": "值"}),
    })
    idx = CfpaIndex.from_zips([primary, secondary])
    assert idx.lookup("create", "item.create.shaft") == "传动杆"
    assert idx.lookup("create", "item.create.gear") == "齿轮"
    assert idx.lookup("other", "k") == "值"
    assert idx.lookup("create", "missing") is None
    assert idx.lookup("nope", "k") is None
    assert len(idx) == 3 and idx.namespace_count == 2


def test_index_skips_broken_files():
    data = jar_bytes({"assets/bad/lang/zh_cn.json": "{oops", "assets/ok/lang/zh_cn.json": lang({"k": "好"})})
    idx = CfpaIndex.from_zips([data])
    assert idx.lookup("ok", "k") == "好"
    assert any("bad" in w for w in idx.warnings)


def test_index_skips_corrupt_zip():
    idx = CfpaIndex.from_zips([b"not a zip"])
    assert len(idx) == 0 and idx.warnings


def test_index_reads_legacy_lang():
    data = jar_bytes({"assets/old/lang/zh_cn.lang": "#PARSE_ESCAPES\nitem.gear.name=齿轮\n"})
    assert CfpaIndex.from_zips([data]).lookup("old", "item.gear.name") == "齿轮"
