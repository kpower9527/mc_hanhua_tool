import pytest

from mctrans.snbt import Scalar, SNBTError, SString, dump_lang, parse, quote, replace_spans, to_lang_value

SAMPLE = r'''{
	default_quest_shape: "gear"
	filename: "create"
	id: "100C477F4E63F20A"
	order_index: 3
	x: -6.0d
	enabled: true
	ints: [I; 1, 2, 3]
	quests: [
		{
			title: "&6Mechanical Plough"
			description: [
				"The &6Plough&r will plough!"
				""
				"Say \"hi\"\\n\\nok"
			]
			"quoted key": 'single \'q\''
		}
	]
}
'''


def test_parse_structure():
    tree = parse(SAMPLE)
    assert isinstance(tree, dict)
    assert tree["filename"].value == "create"
    assert isinstance(tree["order_index"], Scalar) and tree["order_index"].raw == "3"
    assert tree["x"].raw == "-6.0d" and tree["enabled"].raw == "true"
    assert [s.raw for s in tree["ints"]] == ["1", "2", "3"]
    quest = tree["quests"][0]
    assert quest["title"].value == "&6Mechanical Plough"
    assert [s.value for s in quest["description"]] == ["The &6Plough&r will plough!", "", 'Say "hi"\\n\\nok']
    # FTB 规则：单引号字符串里的 \' 属于未知转义，连同反斜杠一起被丢弃
    assert quest["quoted key"].value == "single q"


def test_spans_point_at_literals():
    tree = parse(SAMPLE)
    s = tree["quests"][0]["title"]
    assert SAMPLE[s.start:s.end] == '"&6Mechanical Plough"'


def test_commas_and_compact_syntax():
    tree = parse('{a:"x",b:[ "y" , "z" ],c:{d:1b}}')
    assert tree["a"].value == "x" and [n.value for n in tree["b"]] == ["y", "z"]
    assert tree["c"]["d"].raw == "1b"


@pytest.mark.parametrize("bad", ['{a: "unterminated}', '{a: "x"', '{a "x"}', '{a: "line\nbreak"}', '{} trailing'])
def test_parse_errors(bad):
    with pytest.raises(SNBTError):
        parse(bad)


def test_quote_escapes_like_ftb():
    assert quote('a"b\\c') == r'"a\"b\\c"'
    assert quote("tab\there\nnl") == r'"tab\there\nnl"'
    assert quote("it's") == '"it\'s"'


def test_quote_round_trip():
    for value in ['plain', 'with "quotes"', 'back\\slash', 'lit\\n', 'real\nnewline', '中文 &6颜色&r']:
        assert parse("{k: " + quote(value) + "}")["k"].value == value


def test_to_lang_value():
    tree = parse('{a: "x", b: ["1", "2"], c: 5, d: [1, 2], e: []}')
    assert to_lang_value(tree["a"]) == "x"
    assert to_lang_value(tree["b"]) == ["1", "2"]
    assert to_lang_value(tree["c"]) is None
    assert to_lang_value(tree["d"]) is None
    assert to_lang_value(tree["e"]) == []


def test_dump_lang_round_trip():
    table = {"quest.0A.title": "标题", "quest.0A.quest_desc": ["第一行", "", 'say "hi"'], "weird key": "v", "empty": []}
    text = dump_lang(table)
    assert text.startswith("{\n\tquest.0A.title: \"标题\"\n")
    tree = parse(text)
    assert {k: to_lang_value(v) for k, v in tree.items()} == table


def test_replace_spans_keeps_everything_else():
    tree = parse(SAMPLE)
    quest = tree["quests"][0]
    t, d0 = quest["title"], quest["description"][0]
    out = replace_spans(SAMPLE, [(t.start, t.end, "&6动力犁"), (d0.start, d0.end, "这个&6犁&r会犁地！")])
    assert '"&6动力犁"' in out and '"这个&6犁&r会犁地！"' in out
    assert out.replace('"&6动力犁"', '"&6Mechanical Plough"').replace(
        '"这个&6犁&r会犁地！"', '"The &6Plough&r will plough!"') == SAMPLE
    assert replace_spans(SAMPLE, []) == SAMPLE
