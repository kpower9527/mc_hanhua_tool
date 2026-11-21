from __future__ import annotations

from typing import Protocol

from ..config import ProviderConfig


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatClient:
    """OpenAI 兼容接口客户端。429/5xx/超时由 openai SDK 自动指数退避重试。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0,
                 max_retries: int = 5, temperature: float = 0.2):
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("模型返回了空内容")
        return content


def client_from_config(p: ProviderConfig) -> OpenAICompatClient:
    return OpenAICompatClient(base_url=p.base_url, api_key=p.resolve_api_key(), model=p.model)
