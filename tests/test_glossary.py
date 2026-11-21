from mctrans.translate.glossary import Glossary, build_glossary, load_user_glossary


def test_find_whole_words_longest_first():
    g = Glossary()
    g.add("Andesite", "安山岩")
    g.add("Andesite Alloy", "安山合金")
    g.add("Alloy", "合金")
    hits = g.find(["Craft an andesite alloy first."])
    assert hits == [("Andesite Alloy", "安山合金"), ("Alloy", "合金"), ("Andesite", "安山岩")]


def test_find_requires_word_boundary():
    g = Glossary()
    g.add("Gear", "齿轮")
    assert g.find(["Gearbox"]) == []
    assert g.find(["A gear."]) == [("Gear", "齿轮")]


def test_add_filters_bad_terms():
    g = Glossary()
    g.add("ab", "短")              # 太短
    g.add("Speed %s", "速度 %s")   # 含格式码
    g.add("Gear", "")              # 空译文
    g.add("Gear", "Gear")          # 未翻译
    assert len(g) == 0


def test_find_limit():
    g = Glossary()
    for i in range(10):
        g.add(f"Term{i}", f"术语{i}")
    assert len(g.find([" ".join(f"term{i}" for i in range(10))], limit=3)) == 3


def test_user_terms_take_priority():
    g = build_glossary([("Gear", "齿轮")], [("Gear", "传动齿轮")])
    assert g.find(["Gear"]) == [("Gear", "传动齿轮")]


def test_load_user_glossary(tmp_path):
    p = tmp_path / "glossary.tsv"
    p.write_text("# 注释\nGear\t齿轮\n\nbad line\nShaft\t传动杆\n", encoding="utf-8")
    assert load_user_glossary(p) == [("Gear", "齿轮"), ("Shaft", "传动杆")]
    assert load_user_glossary(tmp_path / "missing.tsv") == []
