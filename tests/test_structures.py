import gzip
import struct

from mctrans import nbt
from mctrans.handlers import all_handlers
from mctrans.handlers.base import OutputSink
from mctrans.handlers.structures import StructureTextHandler, component_text
from mctrans.instance import load_instance
from mctrans.models import TranslatedItem

SPAWN = """{
    DataVersion: 3955,
    size: [44, 41, 45],
    blocks: [
        {pos: [15, 15, 29], state: 0, nbt: {id: "rftoolsutility:creative_screen", items: {Items: [{Slot: 0, components: {"rftoolsutility:module_text_data": {align: "Center", color: 16245054, large: 1b, line: "Welcome to"}}, count: 1, id: "rftoolsutility:text_module"}, {Slot: 1, components: {"rftoolsutility:module_text_data": {line: "The Sky!"}}, count: 1}]}}},
        {pos: [1, 2, 3], state: 1, nbt: {id: "minecraft:sign", front_text: {color: "black", messages: ['"No Place"', '{"text":"Like Home!","color":"red"}', '""', '"***"']}, back_text: {messages: ['""', '""', '""', '""']}}},
        {pos: [1, 2, 4], state: 1, nbt: {Text1: '{"text":"It\\'s old"}', Name: "keep me"}}
    ],
    palette: [{Name: "minecraft:dirt"}]
}
"""


def _s(value: str) -> bytes:
    raw = nbt.encode_mutf8(value)
    return struct.pack(">H", len(raw)) + raw


def _named(tag: int, name: str, payload: bytes) -> bytes:
    return bytes([tag]) + _s(name) + payload


def make_nbt() -> bytes:
    messages = b"\x08" + struct.pack(">i", 4) + _s('"No Place"') + _s('"Like Home!"') + _s('""') + _s('""')
    front = _named(9, "messages", messages) + _named(8, "color", _s("black")) + b"\x00"
    sign = _named(10, "front_text", front) + _named(8, "id", _s("minecraft:sign")) + b"\x00"
    block = _named(10, "nbt", sign) + _named(3, "state", struct.pack(">i", 2)) + b"\x00"
    blocks = b"\x0a" + struct.pack(">i", 1) + block
    body = _named(9, "blocks", blocks) + _named(3, "DataVersion", struct.pack(">i", 3955)) + b"\x00"
    return gzip.compress(_named(10, "", body))


def setup(root):
    d = root / "config" / "skyblockbuilder" / "templates" / "islands"
    d.mkdir(parents=True)
    (d / "spawn.snbt").write_text(SPAWN, encoding="utf-8")
    (d / "home.nbt").write_bytes(make_nbt())
    (d / "plain.nbt").write_bytes(gzip.compress(b"\x0a\x00\x00\x00"))
    q = root / "config" / "ftbquests" / "quests"
    q.mkdir(parents=True)
    (q / "data.snbt").write_text('{ messages: ["skip"] }', encoding="utf-8")


def test_component_text():
    assert component_text('"Hi"') == "Hi"
    assert component_text('{"text":"Hi","bold":true}') == "Hi"
    assert component_text('{"text":"","extra":[{"text":"x"}]}') is None
    assert component_text("Plain words") == "Plain words"


def test_mutf8_round_trip():
    for text in ("中文", "a\x00b", "emoji 😀"):
        assert nbt.decode_mutf8(nbt.encode_mutf8(text)) == text
    assert nbt.encode_mutf8("\x00") == b"\xc0\x80"


def test_registered():
    assert "structures" in [h.name for h in all_handlers()]


def test_extract(instance_dir):
    setup(instance_dir)
    h = StructureTextHandler()
    inst = load_instance(instance_dir)
    assert h.detect(inst)
    got = [(e.source.rsplit("/", 1)[-1], e.original) for e in h.extract(inst)]
    assert got == [
        ("home.nbt", "No Place"), ("home.nbt", "Like Home!"),
        ("spawn.snbt", "Welcome to"), ("spawn.snbt", "The Sky!"),
        ("spawn.snbt", "No Place"), ("spawn.snbt", "Like Home!"), ("spawn.snbt", "It's old"),
    ]
    assert h.warnings == []


def test_write_snbt_and_nbt(instance_dir):
    setup(instance_dir)
    h = StructureTextHandler()
    entries = list(h.extract(load_instance(instance_dir)))
    zh = {"Welcome to": "欢迎来到", "The Sky!": "空岛！", "No Place": "金窝银窝",
          "Like Home!": "不如\"自己\"的狗窝！", "It's old": "旧版告示牌"}
    sink = OutputSink(instance_dir)
    h.write([TranslatedItem(e, zh[e.original], "llm", "done") for e in entries], sink)

    spawn = sink.instance_files["config/skyblockbuilder/templates/islands/spawn.snbt"].decode("utf-8")
    assert 'line: "欢迎来到"' in spawn and 'line: "空岛！"' in spawn
    assert '"{\\"text\\": \\"不如\\\\\\"自己\\\\\\"的狗窝！\\", \\"color\\": \\"red\\"}"' in spawn
    assert 'Name: "keep me"' in spawn and "'\"\"'" in spawn

    # 重新解析写出的文件，确认结构完好且文字已替换
    (instance_dir / "config/skyblockbuilder/templates/islands/spawn.snbt").write_text(spawn, encoding="utf-8")
    home = sink.instance_files["config/skyblockbuilder/templates/islands/home.nbt"]
    (instance_dir / "config/skyblockbuilder/templates/islands/home.nbt").write_bytes(home)
    again = [e.original for e in StructureTextHandler().extract(load_instance(instance_dir))]
    assert again == ["金窝银窝", "不如\"自己\"的狗窝！", "欢迎来到", "空岛！", "金窝银窝", "不如\"自己\"的狗窝！", "旧版告示牌"]
    tree = nbt.parse(nbt.decompress(home)[0])
    assert tree["blocks"][0]["nbt"]["front_text"]["color"].value == "black"
    assert tree["blocks"][0]["nbt"]["id"].value == "minecraft:sign"


def test_corrupt_file_is_warned(instance_dir):
    d = instance_dir / "config" / "x"
    d.mkdir(parents=True)
    (d / "bad.snbt").write_text("{ messages: [ ", encoding="utf-8")
    h = StructureTextHandler()
    assert list(h.extract(load_instance(instance_dir))) == []
    assert len(h.warnings) == 1


def test_context_lists_lines_on_same_block(instance_dir):
    setup(instance_dir)
    entries = {(e.source.rsplit("/", 1)[-1], e.original): e for e in StructureTextHandler().extract(load_instance(instance_dir))}
    assert "No Place / Like Home!" in entries[("home.nbt", "No Place")].context
    assert "Welcome to / The Sky!" in entries[("spawn.snbt", "The Sky!")].context
    assert "同一块" not in entries[("spawn.snbt", "It's old")].context
