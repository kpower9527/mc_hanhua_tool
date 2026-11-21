from __future__ import annotations

from .base import Handler, OutputSink
from .ftbquests import FtbQuestsHandler
from .lang_json import LangJsonHandler
from .patchouli import PatchouliHandler

__all__ = ["Handler", "OutputSink", "all_handlers", "get_handler"]


def all_handlers() -> list[Handler]:
    return [LangJsonHandler(), FtbQuestsHandler(), PatchouliHandler()]


def get_handler(name: str) -> Handler:
    for handler in all_handlers():
        if handler.name == name:
            return handler
    raise KeyError(f"未知的处理器：{name}")
