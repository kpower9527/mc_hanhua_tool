import pytest

from mctrans.cfpa.versions import cfpa_assets
from mctrans.mcversion import parse_version


def test_parse_version():
    assert parse_version("1.20.1") == (1, 20, 1)
    assert parse_version("1.21") == (1, 21, 0)
    assert parse_version("26.1-pre2") == (26, 1, 2)


def keys(mc, loader):
    return [a.key for a in cfpa_assets(mc, loader)]


@pytest.mark.parametrize("mc, loader, expected", [
    ("1.20.1", "forge", ["1.20", "1.20-fabric"]),
    ("1.20.1", "neoforge", ["1.20", "1.20-fabric"]),
    ("1.20.1", "fabric", ["1.20-fabric", "1.20"]),
    ("1.21.1", "unknown", ["1.21", "1.21-fabric"]),
    ("1.19.2", "fabric", ["1.19"]),
    ("1.17.1", "forge", ["1.16", "1.16-fabric"]),
    ("26.1", "fabric", ["26.1-fabric", "26.1"]),
    ("1.12.2", "forge", ["1.12.2"]),
    ("1.14.4", "forge", []),
])
def test_cfpa_assets_selection(mc, loader, expected):
    assert keys(mc, loader) == expected


def test_asset_file_names():
    forge, fabric = cfpa_assets("1.20.1", "forge")
    assert forge.zip_name == "Minecraft-Mod-Language-Modpack-1-20.zip"
    assert forge.md5_name == "1.20.md5"
    assert fabric.zip_name == "Minecraft-Mod-Language-Modpack-1-20-Fabric.zip"
    assert fabric.md5_name == "1.20-fabric.md5"
    assert cfpa_assets("26.1", "forge")[0].zip_name == "Minecraft-Mod-Language-Modpack-26-1.zip"


def test_legacy_asset_names():
    (asset,) = cfpa_assets("1.12.2", "forge")
    assert (asset.zip_name, asset.md5_name) == ("Minecraft-Mod-Language-Modpack.zip", "1.12.2.md5")
