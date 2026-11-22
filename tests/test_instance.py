import json

import pytest

from helpers import make_jar
from mctrans.instance import InstanceError, detect_mc_version, inspect_jars, load_instance


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_version_from_curseforge(instance_dir):
    assert detect_mc_version(instance_dir) == "1.20.1"


def test_version_from_mmc_pack(tmp_path):
    inst = tmp_path / "Prism" / "MyPack"
    root = inst / "minecraft"
    root.mkdir(parents=True)
    write_json(inst / "mmc-pack.json", {"components": [
        {"uid": "net.minecraftforge", "version": "47.2.0"},
        {"uid": "net.minecraft", "version": "1.20.1"},
    ]})
    assert detect_mc_version(root) == "1.20.1"


def test_version_from_hmcl_patches(tmp_path):
    root = tmp_path / "versions" / "ATM9"
    write_json(root / "ATM9.json", {"id": "ATM9", "patches": [{"id": "game", "version": "1.20.1"}]})
    assert detect_mc_version(root) == "1.20.1"


def test_version_from_fml_argument(tmp_path):
    root = tmp_path / "versions" / "pack"
    write_json(root / "pack.json", {"id": "pack", "arguments": {"game": [
        "--fml.forgeVersion", "47.2.0", "--fml.mcVersion", "1.20.1"]}})
    assert detect_mc_version(root) == "1.20.1"


def test_version_from_fabric_intermediary(tmp_path):
    root = tmp_path / "versions" / "fab"
    write_json(root / "fab.json", {"id": "fab", "libraries": [
        {"name": "net.fabricmc:intermediary:1.21.1"}]})
    assert detect_mc_version(root) == "1.21.1"


def test_version_from_inherits_from(tmp_path):
    root = tmp_path / "versions" / "x"
    write_json(root / "x.json", {"id": "x", "inheritsFrom": "1.18.2"})
    assert detect_mc_version(root) == "1.18.2"


def test_version_unknown(tmp_path):
    (tmp_path / "mods").mkdir()
    assert detect_mc_version(tmp_path) is None


def test_inspect_jars_loader_and_i18n(tmp_path):
    jars = [
        make_jar(tmp_path / "a.jar", {"META-INF/mods.toml": 'modId="a"'}),
        make_jar(tmp_path / "b.jar", {"META-INF/mods.toml": 'modId="b"'}),
        make_jar(tmp_path / "c.jar", {"fabric.mod.json": '{"id": "c"}'}),
        make_jar(tmp_path / "I18nUpdateMod-3.6.0.jar", {"META-INF/mods.toml": 'modId="i18nupdatemod"'}),
    ]
    assert inspect_jars(jars) == ("forge", True)


def test_inspect_jars_neoforge_by_modid(tmp_path):
    jars = [
        make_jar(tmp_path / "a.jar", {"META-INF/neoforge.mods.toml": 'modId="a"'}),
        make_jar(tmp_path / "x.jar", {"META-INF/neoforge.mods.toml": 'modId = "i18nupdatemod"'}),
    ]
    assert inspect_jars(jars) == ("neoforge", True)


def test_inspect_jars_skips_broken(tmp_path):
    bad = tmp_path / "bad.jar"
    bad.write_bytes(b"not a zip")
    assert inspect_jars([bad]) == ("unknown", False)


def test_load_instance(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"fabric.mod.json": '{"id": "a"}'})
    (instance_dir / "mods" / "old.jar.disabled").write_bytes(b"")
    inst = load_instance(instance_dir)
    assert inst.mc_version == "1.20.1"
    assert inst.loader == "fabric"
    assert [p.name for p in inst.mod_jars] == ["a.jar"]
    assert inst.has_i18nupdatemod is False


def test_load_instance_override_version(instance_dir):
    assert load_instance(instance_dir, mc_version="1.19.2").mc_version == "1.19.2"


def test_load_instance_requires_mods_dir(tmp_path):
    with pytest.raises(InstanceError, match="mods"):
        load_instance(tmp_path)


def test_load_instance_unknown_version(tmp_path):
    (tmp_path / "mods").mkdir()
    with pytest.raises(InstanceError, match="--mc-version"):
        load_instance(tmp_path)


def test_find_instance_dirs(tmp_path):
    from mctrans.instances import find_instance_dirs
    for name in ("A", "B"):
        (tmp_path / ".minecraft" / "versions" / name / "mods").mkdir(parents=True)
    (tmp_path / ".minecraft" / "versions" / "vanilla").mkdir()
    mc = tmp_path / ".minecraft"
    assert [d.name for d in find_instance_dirs(mc)] == ["A", "B"]
    assert [d.name for d in find_instance_dirs(tmp_path)] == ["A", "B"]
    assert find_instance_dirs(mc / "versions" / "A") == [mc / "versions" / "A"]
    assert find_instance_dirs(mc / "versions" / "vanilla") == []
