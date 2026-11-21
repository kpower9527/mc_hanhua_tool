from __future__ import annotations

from dataclasses import dataclass, field

from .client import LLMClient
from .glossary import Glossary
from .prompt import SYSTEM_PROMPT, build_user_prompt, parse_response
from .protect import TOKEN_RE, TokenMismatch, protect, restore

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


def translate_batch(client: LLMClient, units: list[Unit], namespace: str, kind: str,
                    glossary: Glossary) -> list[UnitResult]:
    masks = [protect(u.original) for u in units]
    items: dict[str, dict[str, str]] = {}
    for i, (u, m) in enumerate(zip(units, masks), 1):
        item = {"text": m.text.strip()}
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
    for i, (u, m) in enumerate(zip(units, masks), 1):
        value = obj.get(str(i))
        if isinstance(value, dict):  # 部分模型会照搬输入结构：{"1": {"text": "译文"}}
            value = value.get("text")
        if not isinstance(value, str) or not value.strip():
            results.append(UnitResult(None, "缺少译文"))
            continue
        try:
            text = _keep_edges(u.original, restore(value.strip(), m))
        except TokenMismatch as exc:
            results.append(UnitResult(None, str(exc)))
            continue
        results.append(UnitResult(text, None, SAME_NOTE if text == u.original else None))
    return results
