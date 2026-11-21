import io
import json
import zipfile

import pytest

from mctrans.output.resourcepack import build_pack_zip, pack_format_for, pack_mcmeta


@pytest.mark.parametrize("version, fmt", [
    ("1.16.5", 6), ("1.16.1", 5), ("1.18.2", 8), ("1.19.2", 9), ("1.19.4", 13),
    ("1.20.1", 15), ("1.20.4", 22), ("1.21.1", 34), ("1.21.4", 46), ("1.21.8", 64),
])
def test_pack_format(version, fmt):
    assert pack_format_for(version) == fmt


def test_pack_format_too_old():
    with pytest.raises(ValueError, match="1.16"):
        pack_format_for("1.12.2")


def test_mcmeta_old_version():
    meta = json.loads(pack_mcmeta("1.20.1", "desc"))
    assert meta == {"pack": {"pack_format": 15, "description": "desc"}}


def test_mcmeta_new_version_has_ranges():
    pack = json.loads(pack_mcmeta("1.21.1", "desc"))["pack"]
    assert pack["pack_format"] == 34 and pack["supported_formats"] == [34, 9999]
    newest = json.loads(pack_mcmeta("1.21.10", "desc"))["pack"]
    assert newest["min_format"] == 69 and newest["max_format"] == 9999


def test_build_pack_zip_deterministic():
    files = {"assets/a/lang/zh_cn.json": b"{}", "assets/b/lang/zh_cn.json": b"{}"}
    meta = pack_mcmeta("1.20.1", "d")
    first = build_pack_zip(files, meta)
    assert first == build_pack_zip(dict(reversed(list(files.items()))), meta)
    with zipfile.ZipFile(io.BytesIO(first)) as z:
        assert z.namelist() == ["pack.mcmeta", "assets/a/lang/zh_cn.json", "assets/b/lang/zh_cn.json"]
