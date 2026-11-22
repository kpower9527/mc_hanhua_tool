from mctrans.output.options import add_pack_top, get_option, read_packs, remove_packs, set_option

SAMPLE = 'version:3465\r\nresourcePacks:["vanilla","file/other.zip"]\r\nlang:en_us\r\n'


def test_read_packs():
    assert read_packs(SAMPLE) == ["vanilla", "file/other.zip"]
    assert read_packs("lang:en_us\n") is None
    assert read_packs("resourcePacks:broken\n") == []


def test_get_and_set_option_keeps_crlf():
    assert get_option(SAMPLE, "lang") == "en_us"
    out = set_option(SAMPLE, "lang", "zh_cn")
    assert out == SAMPLE.replace("lang:en_us", "lang:zh_cn")
    appended = set_option("version:1\n", "lang", "zh_cn")
    assert appended == "version:1\nlang:zh_cn\n"


def test_add_pack_top():
    out = add_pack_top(SAMPLE, "file/maikuai-zh_cn.zip")
    assert read_packs(out) == ["vanilla", "file/other.zip", "file/maikuai-zh_cn.zip"]
    assert read_packs(add_pack_top(out, "file/maikuai-zh_cn.zip")) == read_packs(out)  # 不重复


def test_add_pack_to_empty_options():
    out = add_pack_top("", "file/x.zip")
    assert read_packs(out) == ["vanilla", "file/x.zip"]


def test_remove_packs():
    text = add_pack_top(SAMPLE, "file/x.zip") + 'incompatibleResourcePacks:["file/x.zip"]\r\n'
    out = remove_packs(text, ["file/x.zip"])
    assert read_packs(out) == ["vanilla", "file/other.zip"]
    assert get_option(out, "incompatibleResourcePacks") == "[]"
    assert remove_packs("lang:en_us\n", ["file/x.zip"]) == "lang:en_us\n"


from mctrans.output.options import place_around

ANCHOR = "file/Minecraft-Mod-Language-Modpack-Converted-1.20.1.zip"


def test_place_around_adds_anchor():
    out = place_around('resourcePacks:["vanilla"]\n', ANCHOR, ["file/fill.zip"], ["file/fix.zip"])
    assert read_packs(out) == ["vanilla", "file/fill.zip", ANCHOR, "file/fix.zip"]


def test_place_around_existing_anchor_keeps_others():
    text = f'resourcePacks:["vanilla","file/fill.zip","{ANCHOR}","file/other.zip"]\n'
    out = place_around(text, ANCHOR, ["file/fill.zip"], [])
    assert read_packs(out) == ["vanilla", "file/fill.zip", ANCHOR, "file/other.zip"]
    out = place_around(text, ANCHOR, [], ["file/fix.zip"])
    assert read_packs(out) == ["vanilla", "file/fill.zip", ANCHOR, "file/fix.zip", "file/other.zip"]


def test_default_packs_for_legacy():
    assert read_packs(add_pack_top("", "maikuai-zh_cn.zip", default_packs=[])) == ["maikuai-zh_cn.zip"]
    assert read_packs(place_around("", "A.zip", ["fill.zip"], [], default_packs=[])) == ["fill.zip", "A.zip"]
