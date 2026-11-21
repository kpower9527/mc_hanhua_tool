from __future__ import annotations

from typing import Protocol

from ..config import ProviderConfig


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatClient:
    """OpenAI 兼容接口客户端。429/5xx/超时由 openai SDK 自动指数退避重试。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 90.0,
                 max_retries: int = 2, temperature: float = 0.2, extra_body: dict | None = None):
        import httpx
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self.extra_body = extra_body
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
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("模型返回了空内容")
        return content


def client_from_config(p: ProviderConfig) -> OpenAICompatClient:
    # 只对明确支持的接口发送 enable_thinking，避免其他服务因未知参数报错
    extra = {"enable_thinking": p.enable_thinking} if p.supports_thinking_switch() else None
    return OpenAICompatClient(base_url=p.base_url, api_key=p.resolve_api_key(), model=p.model, extra_body=extra)
