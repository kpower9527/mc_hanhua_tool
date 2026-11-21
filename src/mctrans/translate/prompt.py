from __future__ import annotations

import json
import re

SYSTEM_PROMPT = """你是专业的 Minecraft 模组本地化译者，负责把英文翻译成简体中文。
规则：
1. 输入是一个 JSON 对象，键是编号，值包含 text（原文）和可选的 hint（文本所在位置的键名，用来判断它是物品名、提示还是说明）。
2. 输出一个 JSON 对象，键与输入完全相同，值直接是对应 text 的中文译文字符串（不要再包一层对象）。不得增加或遗漏任何键。
   例如输入 {"1": {"text": "Iron Plate", "hint": "item.example.iron_plate"}}，输出 {"1": "铁板"}。
3. 形如 ⟦0⟧、⟦1⟧ 的标记是占位符，必须原样保留在译文中，数量不变，可以按中文语序调整位置。
4. 必须遵循给出的术语表。物品、方块、生物名称要简洁，符合 Minecraft 官方简体中文和中文社区的习惯译法。
5. 模组名、人名等专有名词可以保留英文。
6. 只输出 JSON，不要输出任何解释。"""

INPUT_MARKER = "待翻译："


def build_user_prompt(namespace: str, kind: str, terms: list[tuple[str, str]],
                      items: dict[str, dict[str, str]]) -> str:
    lines = [f"模组：{namespace}", f"内容类型：{kind}"]
    if terms:
        lines.append("术语表：")
        lines.extend(f"- {en} → {zh}" for en, zh in terms)
    lines.append(INPUT_MARKER)
    lines.append(json.dumps(items, ensure_ascii=False, indent=1))
    return "\n".join(lines)


def parse_response(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```[A-Za-z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < start:
        raise ValueError("响应中没有 JSON 对象")
    obj = json.loads(s[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("响应 JSON 不是对象")
    return obj
