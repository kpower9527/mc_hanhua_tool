import json

import pytest

from helpers import FakeClient
from mctrans.config import ProviderConfig
from mctrans.translate.batch import Unit, translate_batch
from mctrans.translate.client import OpenAICompatClient, client_from_config
from mctrans.translate.glossary import Glossary
from mctrans.translate.prompt import INPUT_MARKER, build_user_prompt, parse_response


def units(*texts):
    return [Unit(t, f"key.{i}", [i]) for i, t in enumerate(texts)]


def test_build_user_prompt_layout():
    p = build_user_prompt("create", "模组语言文件", [("Gear", "齿轮")], {"1": {"text": "A gear", "hint": "k"}})
    assert "模组：create" in p and "- Gear → 齿轮" in p
    assert json.loads(p.split(INPUT_MARKER, 1)[1]) == {"1": {"text": "A gear", "hint": "k"}}


def test_parse_response_variants():
    assert parse_response('{"1": "一"}') == {"1": "一"}
    assert parse_response('```json\n{"1": "一"}\n```') == {"1": "一"}
    assert parse_response('好的：{"1": "一"} 完成') == {"1": "一"}
    with pytest.raises(ValueError):
        parse_response("没有 JSON")
    with pytest.raises(ValueError):
        parse_response("[1]")


def test_success_restores_tokens():
    client = FakeClient(lambda t: t.replace("Hello", "你好"))
    res = translate_batch(client, units("§aHello %s"), "ns", "kind", Glossary())
    assert res[0].text == "§a你好 %s" and res[0].error is None


def test_prompt_contains_masked_text_and_glossary():
    g = Glossary()
    g.add("Gear", "齿轮")
    client = FakeClient()
    translate_batch(client, units("§aGear"), "create", "kind", g)
    sent = json.loads(client.calls[0].split(INPUT_MARKER, 1)[1])
    assert sent == {"1": {"text": "⟦0⟧Gear", "hint": "key.0"}}
    assert "- Gear → 齿轮" in client.calls[0]


def test_missing_key_and_token_loss():
    def fn(text):
        if text == "Skip me":
            return None
        if text.startswith("Lose"):
            return "丢了占位符"
        return "好"
    res = translate_batch(FakeClient(fn), units("Skip me", "Lose %s", "Fine"), "ns", "k", Glossary())
    assert res[0].text is None and "缺少译文" in res[0].error
    assert res[1].text is None and "占位符" in res[1].error
    assert res[2].text == "好"


def test_client_error_fails_all():
    res = translate_batch(FakeClient(fail_times=1), units("A", "B"), "ns", "k", Glossary())
    assert all(r.text is None and "请求失败" in r.error for r in res)


def test_whitespace_edges_preserved():
    res = translate_batch(FakeClient(lambda t: "  好  "), units("  Hello "), "ns", "k", Glossary())
    assert res[0].text == "  好 "


def test_same_as_original_is_noted():
    res = translate_batch(FakeClient(lambda t: t), units("Create"), "ns", "k", Glossary())
    assert res[0].text == "Create" and "相同" in res[0].note


def test_client_from_config():
    c = client_from_config(ProviderConfig("deepseek", "https://api.deepseek.com", "deepseek-chat", api_key="sk-x"))
    assert isinstance(c, OpenAICompatClient) and c.model == "deepseek-chat"


class NestedClient:
    """模拟模型照搬输入结构：{"1": {"text": "译文"}}。"""
    model = "nested"

    def complete(self, system, user):
        items = json.loads(user.split(INPUT_MARKER, 1)[1])
        return json.dumps({k: {"text": "译" + v["text"]} for k, v in items.items()}, ensure_ascii=False)


def test_accepts_nested_text_objects():
    res = translate_batch(NestedClient(), units("Shaft", "Use %s"), "ns", "k", Glossary())
    assert [r.text for r in res] == ["译Shaft", "译Use %s"]


def test_system_prompt_shows_output_example():
    from mctrans.translate.prompt import SYSTEM_PROMPT
    assert '{"1": "' in SYSTEM_PROMPT


def test_thinking_switch_only_for_dashscope():
    qwen = ProviderConfig("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.7-flash", api_key="k")
    assert client_from_config(qwen).extra_body == {"enable_thinking": False}
    qwen.enable_thinking = True
    assert client_from_config(qwen).extra_body == {"enable_thinking": True}
    deepseek = ProviderConfig("deepseek", "https://api.deepseek.com", "deepseek-chat", api_key="k")
    assert client_from_config(deepseek).extra_body is None
