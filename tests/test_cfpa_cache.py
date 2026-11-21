import hashlib

import pytest

from mctrans.cfpa.cache import CfpaCache
from mctrans.cfpa.versions import CfpaAsset

ASSET = CfpaAsset("1.20", "Minecraft-Mod-Language-Modpack-1-20.zip", "1.20.md5")
BASE = "https://example.test/autobuild"


class FakeRemote:
    def __init__(self, data=b"zipdata"):
        self.data = data
        self.md5 = hashlib.md5(data).hexdigest().upper()
        self.calls = []
        self.fail = False

    def __call__(self, url):
        self.calls.append(url.rsplit("/", 1)[1])
        if self.fail:
            raise OSError("network down")
        if url == f"{BASE}/{ASSET.md5_name}":
            return (self.md5 + "\n").encode()
        if url == f"{BASE}/{ASSET.zip_name}":
            return self.data
        raise AssertionError(url)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def remote():
    return FakeRemote()


@pytest.fixture
def clock():
    return Clock()


def make(tmp_path, remote, clock):
    return CfpaCache(tmp_path / "cfpa", BASE, fetch=remote, check_hours=6, clock=clock)


def test_first_download(tmp_path, remote, clock):
    r = make(tmp_path, remote, clock).get(ASSET)
    assert (r.status, r.data) == ("updated", b"zipdata")
    assert remote.calls == ["1.20.md5", "Minecraft-Mod-Language-Modpack-1-20.zip"]
    assert (tmp_path / "cfpa" / ASSET.zip_name).read_bytes() == b"zipdata"


def test_within_interval_uses_cache_without_network(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.calls.clear()
    clock.now += 3600
    r = cache.get(ASSET)
    assert (r.status, r.data, remote.calls) == ("cached", b"zipdata", [])


def test_after_interval_checks_md5_only(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.calls.clear()
    clock.now += 7 * 3600
    r = cache.get(ASSET)
    assert (r.status, remote.calls) == ("fresh", ["1.20.md5"])


def test_remote_changed_redownloads(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.data = b"newer"
    remote.md5 = hashlib.md5(b"newer").hexdigest()
    clock.now += 7 * 3600
    r = cache.get(ASSET)
    assert (r.status, r.data) == ("updated", b"newer")


def test_md5_mismatch_is_rejected(tmp_path, remote, clock):
    remote.md5 = "0" * 32
    r = make(tmp_path, remote, clock).get(ASSET)
    assert r.status == "missing" and r.data is None and "校验" in r.message
    assert not (tmp_path / "cfpa" / ASSET.zip_name).exists()


def test_network_failure_falls_back_to_cache(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.fail = True
    clock.now += 7 * 3600
    r = cache.get(ASSET)
    assert (r.status, r.data) == ("cached", b"zipdata") and "network down" in r.message


def test_network_failure_without_cache(tmp_path, remote, clock):
    remote.fail = True
    r = make(tmp_path, remote, clock).get(ASSET)
    assert (r.status, r.data) == ("missing", None) and "network down" in r.message


def test_offline_mode(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    assert cache.get(ASSET, offline=True).status == "missing"
    cache.get(ASSET)
    remote.calls.clear()
    clock.now += 100 * 3600
    r = cache.get(ASSET, offline=True)
    assert (r.status, r.data, remote.calls) == ("cached", b"zipdata", [])
