from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from ..instance import Instance
from ..models import Entry, TranslatedItem


class OutputSink:
    """收集处理器写出的文件：资源包内的文件和实例目录内的文件。"""

    def __init__(self) -> None:
        self.pack_files: dict[str, bytes] = {}
        self.instance_files: dict[str, bytes] = {}

    def add_pack_file(self, relpath: str, data: bytes) -> None:
        self.pack_files[relpath] = data

    def add_instance_file(self, relpath: str, data: bytes) -> None:
        self.instance_files[relpath] = data


class Handler(ABC):
    name: str = ""
    kind_label: str = ""
    output: str = "pack"  # "pack"：写入资源包；"instance"：直接修改实例内文件

    def __init__(self) -> None:
        self.warnings: list[str] = []

    @abstractmethod
    def detect(self, instance: Instance) -> bool: ...

    @abstractmethod
    def extract(self, instance: Instance) -> Iterator[Entry]: ...

    @abstractmethod
    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None: ...
