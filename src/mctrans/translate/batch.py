from __future__ import annotations

import re
from dataclasses import dataclass, field

from .client import LLMClient
from .glossary import Glossary
from .prompt import SYSTEM_PROMPT, build_user_prompt, parse_response
from .protect import TOKEN_RE, Masked, TokenMismatch, protect, restore, token_differences

SAME_NOTE = "译文与原文相同，可能未翻译"


@dataclass
class Unit:
    """去重后的一条待翻译原文，对应一个或多个条目。"""

    original: str
    hint: str
    entry_ids: list[int] = field(default_factory=list)


@dataclass
class UnitResult:
    text: str | None
    error: str | None = None
    note: str | None = None


def _keep_edges(original: str, text: str) -> str:
    lead = original[: len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()):]
    return lead + text.strip() + trail


EDGE_TOKEN_RE = re.compile(r"⟦(\d+)⟧")
# 只有纯格式标记可以从首尾剥离；%s、{0} 等参数在中文里常要调整语序，必须留给模型处理
FORMAT_ONLY_RE = re.compile(r"^(?:§.|&[0-9a-fk-or]|\\n|\n|\$\([^)]*\))$")


def split_edges(m: Masked) -> tuple[str, str, str]:
    """拆出首尾连续的纯格式标记（如开头的 &a、结尾的 &r）。

    它们与翻译内容无关，交给模型反而经常被丢掉（实测任务书标题大批失败的主因），
    所以只把中间部分发给模型，返回后再原样拼回。
    """
    core = m.text.strip()

    def strippable(match: re.Match) -> bool:
        return bool(FORMAT_ONLY_RE.match(m.tokens[int(match.group(1))]))

    start = 0
    while True:
        rest = core[start:]
        stripped = rest.lstrip()
        match = EDGE_TOKEN_RE.match(stripped)
        if not match or not strippable(match):
            break
        start += len(rest) - len(stripped) + match.end()
    end = len(core)
    while end > start:
        head = core[start:end].rstrip()
        match = None
        for match in EDGE_TOKEN_RE.finditer(head):
            pass
        if match is None or match.end() != len(head) or not strippable(match):
            break
        end = start + match.start()
    middle = core[start:end]
    if not middle.strip():  # 全是格式标记：不拆
        return "", core, ""
    return core[:start], middle, core[end:]


def translate_batch(client: LLMClient, units: list[Unit], namespace: str, kind: str,
                    glossary: Glossary, lenient: bool = False) -> list[UnitResult]:
    masks = [protect(u.original) for u in units]
    edges = [split_edges(m) for m in masks]
    items: dict[str, dict[str, str]] = {}
    for i, (u, (_, middle, _)) in enumerate(zip(units, edges), 1):
        item = {"text": middle}
        if u.hint:
            item["hint"] = u.hint
        items[str(i)] = item
    # 在去掉占位符的文本上匹配术语，避免 "§aGear" 被切成 "agear"
    terms = glossary.find(TOKEN_RE.sub(" ", m.text) for m in masks)
    user = build_user_prompt(namespace, kind, terms, items)
    try:
        obj = parse_response(client.complete(SYSTEM_PROMPT, user))
    except Exception as exc:  # API 错误或响应无法解析：整批失败，交给上层重试
        return [UnitResult(None, f"请求失败：{exc}") for _ in units]

    results: list[UnitResult] = []
    for i, (u, m, (lead, _, trail)) in enumerate(zip(units, masks, edges), 1):
        value = obj.get(str(i))
        if isinstance(value, dict):  # 部分模型会照搬输入结构：{"1": {"text": "译文"}}
            value = value.get("text")
        if not isinstance(value, str) or not value.strip():
            results.append(UnitResult(None, "缺少译文"))
            continue
        full = lead + value.strip() + trail
        try:
            text = _keep_edges(u.original, restore(full, m, allow_cosmetic_mismatch=lenient))
        except TokenMismatch as exc:
            results.append(UnitResult(None, str(exc)))
            continue
        note = SAME_NOTE if text == u.original else None
        missing, extra = token_differences(full, m)
        if missing or extra:
            parts = []
            if missing:
                parts.append("缺少 " + " ".join(t.replace("\n", "换行") for t in missing))
            if extra:
                parts.append("多出 " + " ".join(t.replace("\n", "换行") for t in extra))
            note = f"译文的颜色或换行格式与原文不完全一致（{'，'.join(parts)}），建议审校"
        results.append(UnitResult(text, None, note))
    return results
