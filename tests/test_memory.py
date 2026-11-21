from mctrans.memory import Memory, MemoryRecord


def test_remember_and_lookup(tmp_path):
    with Memory(tmp_path / "m.db") as m:
        assert m.remember([MemoryRecord("create", "Shaft", "传动杆", "llm", model="x")]) == 1
        r = m.lookup("create", "Shaft")
        assert (r.text, r.origin, r.confirmed, r.model) == ("传动杆", "llm", False, "x")
        assert m.lookup("other", "Shaft") is None
        assert len(m) == 1


def test_confirmed_not_overwritten_by_unconfirmed(tmp_path):
    with Memory(tmp_path / "m.db") as m:
        m.remember([MemoryRecord("a", "Gear", "齿轮", "human", confirmed=True)])
        assert m.remember([MemoryRecord("a", "Gear", "机翻", "llm")]) == 0
        assert m.lookup("a", "Gear").text == "齿轮"
        assert m.remember([MemoryRecord("a", "Gear", "新齿轮", "human", confirmed=True)]) == 1
        assert m.lookup("a", "Gear").text == "新齿轮"


def test_default_location(mctrans_home):
    with Memory.default() as m:
        assert m.path == mctrans_home / "memory.db"
