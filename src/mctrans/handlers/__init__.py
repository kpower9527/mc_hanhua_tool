from __future__ import annotations

from .base import Handler, OutputSink
from .lang_json import LangJsonHandler

__all__ = ["Handler", "OutputSink", "all_handlers", "get_handler"]


def all_handlers() -> list[Handler]:
    return [LangJsonHandler()]


def get_handler(name: str) -> Handler:
    for handler in all_handlers():
        if handler.name == name:
            return handler
    raise KeyError(f"未知的处理器：{name}")
