from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path


def jar_bytes(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_jar(path: Path, files: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jar_bytes(files))
    return path


def lang(d: dict[str, str]) -> str:
    return json.dumps(d, ensure_ascii=False)


class FakeClient:
    """模拟 LLM：解析 user 消息中 INPUT_MARKER 之后的 JSON，逐条调用 fn。"""

    model = "fake-model"

    def __init__(self, fn=None, fail_times: int = 0):
        self.fn = fn or (lambda text: "译" + text)
        self.fail_times = fail_times
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        from mctrans.translate.prompt import INPUT_MARKER

        self.calls.append(user)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("模拟 API 错误")
        items = json.loads(user.split(INPUT_MARKER, 1)[1])
        out = {}
        for key, value in items.items():
            result = self.fn(value["text"])
            if result is not None:
                out[key] = result
        return json.dumps(out, ensure_ascii=False)
