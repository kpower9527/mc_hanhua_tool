import json

from mctrans.handlers.base import OutputSink
from mctrans.handlers.betterquesting import BetterQuestingHandler
from mctrans.instance import load_instance
from mctrans.models import TranslatedItem

DEFAULT = {
    "format:8": "2.0.0",
    "questDatabase:9": {
        "0:10": {
            "questID:3": 0,
            "properties:10": {"betterquesting:10": {
                "name:8": "§6Getting Started",
                "desc:8": "Welcome!\nCraft a §aWorkbench§r.",
                "icon:10": {"id:8": "minecraft:crafting_table"},
            }},
        },
        "1:10": {"questID:3": 1, "properties:10": {"betterquesting:10": {"name:8": "quest.mypack.q1", "desc:8": ""}}},
    },
    "questLines:9": {"0:10": {"properties:10": {"betterquesting:10": {"name:8": "Chapter One", "desc:8": "The start"}}}},
}


def setup(root):
    d = root / "config" / "betterquesting"
    d.mkdir(parents=True)
    (d / "DefaultQuests.json").write_text(json.dumps(DEFAULT, indent=2), encoding="utf-8")
    split = d / "DefaultQuests" / "Quests" / "NoQuestLine"
    split.mkdir(parents=True)
    (split / "5.json").write_text(json.dumps(
        {"questID:3": 5, "properties:10": {"betterquesting:10": {"name:8": "Split Quest"}}}), encoding="utf-8")


def test_extract(instance_dir):
    setup(instance_dir)
    h = BetterQuestingHandler()
    inst = load_instance(instance_dir)
    assert h.detect(inst)
    got = [(e.source.rsplit("/", 1)[1], e.original, e.context) for e in h.extract(inst)]
    assert got == [
        ("DefaultQuests.json", "§6Getting Started", "DefaultQuests.json#name:8"),
        ("DefaultQuests.json", "Welcome!\nCraft a §aWorkbench§r.", "DefaultQuests.json#desc:8"),
        ("DefaultQuests.json", "Chapter One", "DefaultQuests.json#name:8"),
        ("DefaultQuests.json", "The start", "DefaultQuests.json#desc:8"),
        ("5.json", "Split Quest", "5.json#name:8"),
    ]


def test_detect_without_files(instance_dir):
    assert not BetterQuestingHandler().detect(load_instance(instance_dir))


def test_write(instance_dir):
    setup(instance_dir)
    h = BetterQuestingHandler()
    entries = list(h.extract(load_instance(instance_dir)))
    sink = OutputSink(instance_dir)
    h.write([TranslatedItem(entries[0], "§6新手入门", "llm", "done"),
             TranslatedItem(entries[3], "开端", "llm", "done")], sink)
    out = json.loads(sink.instance_files["config/betterquesting/DefaultQuests.json"].decode("utf-8"))
    bq = out["questDatabase:9"]["0:10"]["properties:10"]["betterquesting:10"]
    assert bq["name:8"] == "§6新手入门" and bq["desc:8"] == DEFAULT["questDatabase:9"]["0:10"]["properties:10"]["betterquesting:10"]["desc:8"]
    assert out["questLines:9"]["0:10"]["properties:10"]["betterquesting:10"]["desc:8"] == "开端"
    assert list(sink.instance_files) == ["config/betterquesting/DefaultQuests.json"]
