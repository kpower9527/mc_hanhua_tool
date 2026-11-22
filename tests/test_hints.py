import pytest

from mctrans.translate.hints import compact_hint


@pytest.mark.parametrize("hint, original, ns, expected", [
    ("chapter.05E614FDA677D85E.title", "Getting Started", "ftbquests", "chapter.title"),
    ("quest.6720F863B907A5F1.quest_desc", "Ability", "ftbquests", "quest.quest_desc"),
    ("item.mekanism.steel_ingot", "Steel Ingot", "mekanism", "item"),
    ("item.immersiveengineering.shader.name.vanguard", "Vanguard", "immersiveengineering", "item.shader.name"),
    ("createaddition.ponder.connector.text_4", "Consumers need...", "createaddition", "ponder.connector.text_4"),
    ("tooltip.foo", "Foo", "foo", "tooltip"),  # 只有两段时不删命名空间同名段
    ("foo", "Foo", "", "foo"),  # 只有一段时不删成空
    ("erguide/entries/turbine/part-forgechargingport.json#name", "Forge Charging Port", "patchouli", "turbine/part-forgechargingport#name"),
    ("book/en_us/entries/misc/intro.json#pages[2].text", "Long text", "patchouli", "misc/intro#pages[2].text"),
    ("", "x", "", ""),
])
def test_compact_hint(hint, original, ns, expected):
    assert compact_hint(hint, original, ns) == expected


def test_long_hint_is_truncated():
    assert len(compact_hint("a" * 200, "x")) == 60
