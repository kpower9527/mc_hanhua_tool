from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from ..config import ProviderConfig


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


@dataclass
class Usage:
    """接口返回的实际用量（多线程累加）。"""

    requests: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def cost(self, price_input: float, price_output: float) -> float:
        return self.input_tokens / 1e6 * price_input + self.output_tokens / 1e6 * price_output


def _num(obj, name: str) -> int:
    value = getattr(obj, name, None) if obj is not None else None
    return value if isinstance(value, int) else 0


class OpenAICompatClient:
    """OpenAI 兼容接口客户端。429/5xx/超时由 openai SDK 自动指数退避重试。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 90.0,
                 max_retries: int = 2, temperature: float = 0.2, extra_body: dict | None = None):
        import httpx
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self.extra_body = extra_body
        self.usage = Usage()
        self._lock = threading.Lock()
        # 超时不宜过长：引擎自己会把失败的条目拆小重试，单个请求挂住不应拖住整个任务
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=max_retries,
                              timeout=httpx.Timeout(timeout, connect=15.0))

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self.temperature,
            response_format={"type": "json_object"},
            extra_body=self.extra_body,
        )
        u = getattr(resp, "usage", None)
        if u is not None:
            with self._lock:
                self.usage.requests += 1
                self.usage.input_tokens += _num(u, "prompt_tokens")
                self.usage.output_tokens += _num(u, "completion_tokens")
                self.usage.cached_tokens += _num(getattr(u, "prompt_tokens_details", None), "cached_tokens")
                self.usage.reasoning_tokens += _num(getattr(u, "completion_tokens_details", None), "reasoning_tokens")
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("模型返回了空内容")
        return content


def client_from_config(p: ProviderConfig) -> OpenAICompatClient:
    # 只对明确支持的接口发送 enable_thinking，避免其他服务因未知参数报错
    extra = {"enable_thinking": p.enable_thinking} if p.supports_thinking_switch() else None
    return OpenAICompatClient(base_url=p.base_url, api_key=p.resolve_api_key(), model=p.model, extra_body=extra)
