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
