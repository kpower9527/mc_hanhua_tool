import pytest

from mctrans.handlers.base import OutputSink
from mctrans.handlers.scripts import CraftTweakerHandler, KubeJsHandler, kubejs_wants, targets, zs_wants
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem

TOOLTIPS = """ItemEvents.modifyTooltips(event => {
    event.add('allthemodium:teleport_pad', [Text.of("Mining Dimension Disabled")])
    event.add('mekanism:meka_tool', [Text.red('Increased Energy!'), Text.translatable('tip.key')])
    event.add('x:y', Text.of('minecraft:stone'))
})
"""
STARTUP = "﻿StartupEvents.registry('item', e => {\n    e.create('soil').displayName('§bMagical Soil')\n})\n"
ZS = """<x:bandage>.addTooltip(format.green("Blast Resistance: Weak"));
<minecraft:stone>.displayName = "Smooth Rock";
recipes.addShaped("lolarecipe6", <x:y>, [[<minecraft:paper>]]);
"""


def put(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


@pytest.fixture
def scripted(instance_dir):
    put(instance_dir, "kubejs/client_scripts/tooltips.js", TOOLTIPS)
    put(instance_dir, "kubejs/startup_scripts/items/custom.js", STARTUP)
    put(instance_dir, "kubejs/README.txt", "Text.of('not a script')")
    put(instance_dir, "scripts/tweaks.zs", ZS)
    return instance_dir


@pytest.mark.parametrize("callee, ok", [
    ("Text.of", True), ("Text.red", True), ("Component.literal", True), (".displayName", True),
    ("item.tooltip", True), ("Text.translatable", False), ("event.add", False), (None, False),
])
def test_kubejs_wants(callee, ok):
    assert kubejs_wants(callee) is ok


@pytest.mark.parametrize("callee, ok", [
    ("=displayName", True), ("format.green", True), (".addTooltip", True), ("mods.jei.JEI.addDescription", True),
    ("recipes.addShaped", False), (None, False),
])
def test_zs_wants(callee, ok):
    assert zs_wants(callee) is ok


def test_targets_skip_ids():
    assert [t.value for t in targets(TOOLTIPS, True, kubejs_wants)] == ["Mining Dimension Disabled", "Increased Energy!"]


def test_kubejs_extract(scripted):
    h = KubeJsHandler()
    inst = load_instance(scripted)
    assert h.detect(inst)
    got = [(e.source, e.locator, e.original, e.context) for e in h.extract(inst)]
    assert got == [
        ("kubejs/client_scripts/tooltips.js", "0", "Mining Dimension Disabled", "tooltips.js Text.of"),
        ("kubejs/client_scripts/tooltips.js", "1", "Increased Energy!", "tooltips.js Text.red"),
        ("kubejs/startup_scripts/items/custom.js", "0", "§bMagical Soil", "custom.js .displayName"),
    ]


def test_crafttweaker_extract(scripted):
    got = [(e.locator, e.original) for e in CraftTweakerHandler().extract(load_instance(scripted))]
    assert got == [("0", "Blast Resistance: Weak"), ("1", "Smooth Rock")]


def test_detect_without_scripts(instance_dir):
    inst = load_instance(instance_dir)
    assert not KubeJsHandler().detect(inst) and not CraftTweakerHandler().detect(inst)


def item(handler, source, locator, original, text):
    return TranslatedItem(Entry(handler, source, locator, handler, original), text, "llm", "done")


def test_write_kubejs_keeps_bom_and_quotes(scripted):
    sink = OutputSink(scripted)
    KubeJsHandler().write([
        item("kubejs", "kubejs/startup_scripts/items/custom.js", "0", "§bMagical Soil", "§b魔法土壤"),
        item("kubejs", "kubejs/client_scripts/tooltips.js", "1", "Increased Energy!", "能量提升！"),
        item("kubejs", "kubejs/client_scripts/tooltips.js", "0", "Something else", "不匹配，跳过"),
    ], sink)
    startup = sink.instance_files["kubejs/startup_scripts/items/custom.js"].decode("utf-8")
    assert startup == STARTUP.replace("'§bMagical Soil'", "'§b魔法土壤'")
    tooltips = sink.instance_files["kubejs/client_scripts/tooltips.js"].decode("utf-8")
    assert tooltips == TOOLTIPS.replace("'Increased Energy!'", "'能量提升！'")


def test_write_crafttweaker(scripted):
    sink = OutputSink(scripted)
    CraftTweakerHandler().write([item("crafttweaker", "scripts/tweaks.zs", "1", "Smooth Rock", "光滑的\"岩石\"")], sink)
    assert sink.instance_files["scripts/tweaks.zs"].decode("utf-8") == ZS.replace('"Smooth Rock"', r'"光滑的\"岩石\""')
