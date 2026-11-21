from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def mctrans_home(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    monkeypatch.setenv("MCTRANS_HOME", str(home))
    for env in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return home


@pytest.fixture
def instance_dir(tmp_path):
    root = tmp_path / "pack"
    (root / "mods").mkdir(parents=True)
    (root / "minecraftinstance.json").write_text(
        json.dumps({"gameVersion": "1.20.1"}), encoding="utf-8"
    )
    return root
