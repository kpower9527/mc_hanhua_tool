from pathlib import Path

from mctrans import service
from mctrans.config import ExtraRule, load_settings, matching_extras
from mctrans.settings_io import save_settings


def test_rule_matching():
    rules = [ExtraRule("all"), ExtraRule("v120", "1.20"), ExtraRule("v1201f", "1.20.1", "forge"),
             ExtraRule("fabric", "", "fabric"), ExtraRule("v119", "1.19.2")]
    names = lambda v, l: [r.dir for r in matching_extras(rules, v, l)]
    assert names("1.20.1", "forge") == ["all", "v120", "v1201f"]
    assert names("1.20.1", "fabric") == ["all", "fabric", "v120"]
    assert names("1.20", "neoforge") == ["all", "v120"]
    assert names("1.201", "forge") == ["all"]
    assert names("1.19.2", "unknown") == ["all", "v119"]
    assert names(None, None) == ["all"]


def test_legacy_single_dir(mctrans_home):
    Path(mctrans_home).mkdir(parents=True, exist_ok=True)
    (Path(mctrans_home) / "config.toml").write_text('patch_extras_dir = "D:/ad"\n', encoding="utf-8")
    assert load_settings().patch_extras == [ExtraRule("D:/ad")]


def test_service_export_picks_matching_rule(mctrans_home, instance_dir, tmp_path):
    from mctrans.models import Entry
    from mctrans.store import TranslationUpdate, Workspace
    ws = Workspace.for_instance(instance_dir)
    ws.set_meta("mc_version", "1.20.1")
    ws.set_meta("loader", "forge")
    ws.sync_entries("lang_json", [Entry("lang_json", "jar:assets/a/lang/en_us.json", "k", "a", "Gear", "k")])
    ws.save_translations([TranslationUpdate(ws.pending_entries()[0].id, "齿轮", "llm", "done", "m")])
    ws.close()
    dirs = {}
    for name in ("forge", "fabric"):
        d = tmp_path / name / "mods"
        d.mkdir(parents=True)
        (d / f"ad-{name}.jar").write_bytes(b"x")
        dirs[name] = str(tmp_path / name)
    s = load_settings()
    s.patch_extras = [ExtraRule(dirs["forge"], "1.20", "forge"), ExtraRule(dirs["fabric"], "1.20", "fabric")]
    save_settings(s)
    report = service.export_patch(instance_dir, tmp_path / "out", "standalone")
    assert report.extras == ["mods/ad-forge.jar"] and report.extra_dirs == [dirs["forge"]]
