from mctrans.scriptlex import encode, scan

JS = r"""
// Text.of('in a comment')
/* .displayName('block comment') */
ItemEvents.modifyTooltips(event => {
    event.add('mekanism:meka_tool', [
        Text.red('Increased Energy Consumption!'),
        Text.of("§7It's \"quoted\"\n2nd")
    ])
    let re = /mekanism:(meka|tool)'s/g
    let half = total / 2 / count
    let tpl = `plain template`
    let expr = `has ${name} inside`
    event.add(/mekanism:mekasuit_/, [Text.translatable('tooltip.key')])
})
StartupEvents.registry('item', e => {
    e.create('magical_soil').displayName('§bMagical Soil').texture('kubejs:item/x')
})
"""


def lits(text, js=True):
    return [(l.value, l.callee) for l in scan(text, js)]


def test_js_strings_and_callees():
    assert lits(JS) == [
        ("mekanism:meka_tool", "event.add"),
        ("Increased Energy Consumption!", "Text.red"),
        ('§7It\'s "quoted"\n2nd', "Text.of"),
        ("plain template", "=tpl"),
        ("tooltip.key", "Text.translatable"),
        ("item", "StartupEvents.registry"),
        ("magical_soil", "e.create"),
        ("§bMagical Soil", ".displayName"),
        ("kubejs:item/x", ".texture"),
    ]


def test_spans_and_quotes():
    found = scan(JS, True)
    lit = next(l for l in found if l.callee == "Text.of")
    assert JS[lit.start:lit.end] == '"§7It\'s \\"quoted\\"\\n2nd"' and lit.quote == '"'
    soil = next(l for l in found if l.callee == ".displayName")
    assert JS[soil.start:soil.end] == "'§bMagical Soil'" and soil.quote == "'"


def test_unterminated_string_is_skipped():
    assert lits("Text.of('broken\nText.of('ok')") == [("ok", "Text.of")]


ZS = """
#priority 100
# recipes.addShaped("in comment")
<minecraft:stone>.displayName = "Smooth Rock";
<roughtweaks:bandage>.addTooltip(format.green("Blast Resistance: Weak"));
<x:y>.addTooltip("Fertile Seasons:");
recipes.addShaped("lolarecipe6", <roughtweaks:bandage>, [[<minecraft:paper>]]);
var a = 10 / 2;
"""


def test_zs_strings_and_callees():
    assert lits(ZS, js=False) == [
        ("Smooth Rock", "=displayName"),
        ("Blast Resistance: Weak", "format.green"),
        ("Fertile Seasons:", ".addTooltip"),
        ("lolarecipe6", "recipes.addShaped"),
    ]


def test_encode():
    assert encode("它's \"好\"\n", '"') == r'"它' + "'" + r's \"好\"\n"'
    assert encode("it's", "'") == r"'it\'s'"
    assert encode("a`b", "`") == r"`a\`b`"
    assert encode("back\\slash", '"') == r'"back\\slash"'
    for value in ["it's", 'say "hi"', "two\nlines", "tab\there", "中文 §a颜色"]:
        for q in "'\"":
            assert [l.value for l in scan("Text.of(" + encode(value, q) + ")", True)] == [value]
