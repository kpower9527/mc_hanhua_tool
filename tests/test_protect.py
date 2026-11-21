import pytest

from mctrans.translate.protect import TokenMismatch, has_translatable_text, protect, restore


@pytest.mark.parametrize("text, tokens", [
    ("§aGreen §lBold§r", ["§a", "§l", "§r"]),
    ("Has %s items and %1$s or %d%%", ["%s", "%1$s", "%d", "%%"]),
    ("Speed: %.2f", ["%.2f"]),
    ("Hello {0}, {player}!", ["{0}", "{player}"]),
    ("Use $(item)Wrench$() here$(br)", ["$(item)", "$()", "$(br)"]),
    ("Line1\nLine2", ["\n"]),
    ("Line1\\nLine2", ["\\n"]),
    ("&aColored", ["&a"]),
])
def test_protect_tokens(text, tokens):
    assert protect(text).tokens == tokens


def test_literal_backslash_n_is_one_token():
    # 回归测试：字面量 \n（反斜杠 + n）必须整体保护，翻译时丢失会被拒绝
    m = protect("Mechanical Plough\\n\\nBlocks")
    assert m.tokens == ["\\n", "\\n"] and "\\" not in m.text
    with pytest.raises(TokenMismatch):
        restore("机械犁 ⟦0⟧ 方块", m)
    assert restore("机械犁⟦0⟧⟦1⟧方块", m) == "机械犁\\n\\n方块"


def test_protect_leaves_plain_text():
    for text in ("Rock&roll", "50% of them", "R & D", "100% Accurate"):
        assert protect(text).tokens == []


def test_round_trip_with_reorder():
    m = protect("§a%s§r killed %s")
    assert m.text == "⟦0⟧⟦1⟧⟦2⟧ killed ⟦3⟧"
    assert restore("⟦3⟧ 杀死了 ⟦0⟧⟦1⟧⟦2⟧", m) == "%s 杀死了 §a%s§r"


def test_restore_rejects_missing_token():
    m = protect("Hello %s")
    with pytest.raises(TokenMismatch):
        restore("你好", m)


def test_restore_rejects_duplicate_token():
    m = protect("Hello %s")
    with pytest.raises(TokenMismatch):
        restore("你好 ⟦0⟧ ⟦0⟧", m)


def test_has_translatable_text():
    assert has_translatable_text(protect("§a%s").text) is False
    assert has_translatable_text(protect("123 / 456").text) is False
    assert has_translatable_text(protect("§aHello").text) is True


def test_ftb_style_tokens():
    assert protect("The &6Plough&r is &aGreen&r.").tokens == ["&6", "&r", "&a", "&r"]
    assert protect("{@pagebreak}").tokens == ["{@pagebreak}"]
    assert protect("See {image:atm:textures/a.png width:50}").tokens == ["{image:atm:textures/a.png width:50}"]
    assert protect("Rock&roll &amp").tokens == []


def test_restore_allow_cosmetic_mismatch():
    m = protect("Use the &6Plough&r on %s")
    # 只少颜色码：宽松模式接受，严格模式拒绝
    assert restore("对 ⟦2⟧ 使用犁", m, allow_cosmetic_mismatch=True) == "对 %s 使用犁"
    with pytest.raises(TokenMismatch):
        restore("对 ⟦2⟧ 使用犁", m)
    # 多出颜色码：外观问题，宽松模式接受
    assert restore("⟦0⟧⟦0⟧犁⟦1⟧ ⟦2⟧", m, allow_cosmetic_mismatch=True) == "&6&6犁&r %s"
    # 少了或多出参数：宽松模式也拒绝
    with pytest.raises(TokenMismatch):
        restore("对 ⟦0⟧犁⟦1⟧ 使用", m, allow_cosmetic_mismatch=True)
    with pytest.raises(TokenMismatch):
        restore("⟦0⟧犁⟦1⟧ ⟦2⟧ ⟦2⟧", m, allow_cosmetic_mismatch=True)
    # 字面量换行（反斜杠 + n）缺少：外观问题，接受
    n = protect("Line one\\n\\nLine two")
    assert restore("第一行第二行", n, allow_cosmetic_mismatch=True) == "第一行第二行"
