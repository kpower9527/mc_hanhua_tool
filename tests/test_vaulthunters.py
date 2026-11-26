import json

from mctrans.handlers.base import OutputSink
from mctrans.handlers.vaulthunters import (
    VaultHuntersHandler, decode_rich, encode_rich, rich_styles,
)
from mctrans.instance import load_instance
from mctrans.models import TranslatedItem
from mctrans.translate.protect import protect, restore

V = "config/the_vault"
DESC = [
    {"text": "Welcome to ", "color": "$text"},
    {"text": "Vault Hunters", "color": "$questHighlight"},
    {"text": "! Read the book.", "color": "$text"},
]
QUESTS = {
    "quests": [
        {"id": "welcome", "name": "Introduction", "descriptionData": {"description": DESC},
         "presentation": {"goal": "Read the lesson."}},
        {"id": "iron", "name": "Chromatic Iron",
         "descriptionData": {"description": [{"text": "Find iron.", "color": "$text"}]}},
    ],
    "legacyQuests": [],
    "chapters": [{"id": "first", "title": "First Steps", "tab": "Start",
                  "subjects": [{"id": "w", "title": "A New World", "quests": ["welcome"]}]}],
}
ZH_QUESTS = {"quests": [
    {"id": "iron", "name": "彩色铁", "descriptionData": {"description": [{"text": "找到铁。", "color": "$text"}]}},
    {"id": "welcome", "name": "介绍", "descriptionData": {"description": [
        {"text": "旧版欢迎词", "color": "$text"}, {"text": "难度", "color": "$manaCost"}]}},
]}


def write_json(root, rel, obj, ascii_=False):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=ascii_), encoding="utf-8")


def make_pack(root):
    write_json(root, f"{V}/quest/quests.json", QUESTS)
    write_json(root, f"{V}/lang/zh_cn/quest/quests.json", ZH_QUESTS)
    write_json(root, f"{V}/tooltip.json", {"tooltips": [{"item": "the_vault:a", "value": "<gray>Rare <aqua>item"}]})
    write_json(root, f"{V}/lang/zh_cn/greed/trials_screen.json", {"title": "贪婪试炼"})


def test_rich_roundtrip():
    base, others = rich_styles(DESC)
    text = encode_rich(DESC, base, others)
    assert text == "Welcome to <s1>Vault Hunters</s1>! Read the book."
    assert decode_rich(text, base, others) == DESC
    # 译文调整语序后仍能还原样式
    assert decode_rich("欢迎来到<s1>宝库猎人</s1>！", base, others) == [
        {"text": "欢迎来到", "color": "$text"}, {"text": "宝库猎人", "color": "$questHighlight"},
        {"text": "！", "color": "$text"}]


def test_markers_and_colors_are_protected():
    masked = protect("<gray>Your <s1>mana</s1> and <#22fc25>health")
    assert "gray" not in masked.text and "s1" not in masked.text and "22fc25" not in masked.text
    # 颜色标签丢失可以放宽接受，片段标记不行
    assert restore("你的 ⟦1⟧法力⟦2⟧ 和 生命", masked, allow_cosmetic_mismatch=True)


def test_extract(instance_dir):
    make_pack(instance_dir)
    entries = {(e.source, e.locator): e for e in VaultHuntersHandler().extract(load_instance(instance_dir))}
    loc = f"{V}/lang/zh_cn/quest/quests.json"
    welcome = entries[(loc, "welcome.description")]
    assert welcome.original == "Welcome to <s1>Vault Hunters</s1>! Read the book."
    assert welcome.existing_zh is None  # 自带中文的片段结构不同（旧版），不采用
    assert entries[(loc, "iron.description")].existing_zh == "找到铁。"
    assert entries[(loc, "iron.name")].existing_zh == "彩色铁"
    main = f"{V}/quest/quests.json"
    assert entries[(main, "welcome.goal")].original == "Read the lesson."
    assert entries[(main, "chapter:first.title")].original == "First Steps"
    assert entries[(main, "chapter:first/w.title")].original == "A New World"
    assert entries[(f"{V}/lang/zh_cn/tooltip.json", "the_vault:a")].original == "<gray>Rare <aqua>item"


def test_write(instance_dir):
    make_pack(instance_dir)
    h = VaultHuntersHandler()
    entries = {(e.source, e.locator): e for e in h.extract(load_instance(instance_dir))}
    loc = f"{V}/lang/zh_cn/quest/quests.json"
    main = f"{V}/quest/quests.json"
    items = [
        TranslatedItem(entries[(loc, "welcome.description")], "欢迎来到<s1>宝库猎人</s1>！读读这本书。", "llm", "done"),
        TranslatedItem(entries[(loc, "welcome.name")], "入门", "llm", "done"),
        TranslatedItem(entries[(main, "chapter:first.title")], "第一步", "llm", "done"),
    ]
    sink = OutputSink(instance_dir)
    h.write(items, sink)
    raw = sink.instance_files[loc]
    raw.decode("ascii")  # 必须是纯 ASCII，避免 GBK 乱码
    out = json.loads(raw)
    q = {x["id"]: x for x in out["quests"]}
    assert set(q) == {"welcome", "iron"}  # 以英文配置为准，补齐自带中文缺的任务
    assert q["welcome"]["name"] == "入门"
    assert q["welcome"]["descriptionData"]["description"][1] == {"text": "宝库猎人", "color": "$questHighlight"}
    assert q["iron"]["name"] == "彩色铁"  # 没有新译文时沿用自带中文
    main_out = json.loads(sink.instance_files[main].decode("ascii"))
    assert main_out["chapters"][0]["title"] == "第一步"
    assert main_out["quests"][0]["presentation"]["goal"] == "Read the lesson."
    # 没有英文源的自带中文文件也转成 ASCII
    trials = sink.instance_files[f"{V}/lang/zh_cn/greed/trials_screen.json"]
    assert json.loads(trials.decode("ascii")) == {"title": "贪婪试炼"}
    # 没有任何中文的文件不生成
    assert f"{V}/lang/zh_cn/tooltip.json" not in sink.instance_files
