from mctrans.handlers.base import OutputSink
from mctrans.handlers.config_text import ConfigTextHandler, load_rules
from mctrans.instance import load_instance
from mctrans.models import TranslatedItem

TIPS = "# tips\ntip=Use a §aWrench§r to rotate.\ntip=Press F3 for debug info.\nother=ignored\n"
RULES = """
[[rule]]
glob = "config/tips/*.txt"
pattern = '^tip=(?P<text>.+)$'
"""


def setup(instance_dir, mctrans_home, rules=RULES):
    (instance_dir / "config" / "tips").mkdir(parents=True)
    (instance_dir / "config" / "tips" / "tips.txt").write_bytes(TIPS.encode("utf-8"))
    mctrans_home.mkdir(parents=True, exist_ok=True)
    (mctrans_home / "config_rules.toml").write_text(rules, encoding="utf-8")


def test_load_rules_merges_instance_rules(instance_dir, mctrans_home):
    setup(instance_dir, mctrans_home)
    (instance_dir / ".mctrans").mkdir()
    (instance_dir / ".mctrans" / "config_rules.toml").write_text(
        '[[rule]]\nglob = "config/a.json"\npattern = \'"title": "(?P<text>[^"]+)"\'\n', encoding="utf-8")
    rules, warnings = load_rules(instance_dir)
    assert [r.glob for r in rules] == ["config/tips/*.txt", "config/a.json"] and warnings == []


def test_load_rules_reports_bad_rules(instance_dir, mctrans_home):
    setup(instance_dir, mctrans_home, rules='[[rule]]\nglob = "x"\npattern = "no group"\n[[rule]]\nglob = "y"\npattern = "(?P<text>["\n')
    rules, warnings = load_rules(instance_dir)
    assert rules == [] and len(warnings) == 2


def test_extract_and_write(instance_dir, mctrans_home):
    setup(instance_dir, mctrans_home)
    h = ConfigTextHandler()
    inst = load_instance(instance_dir)
    assert h.detect(inst)
    entries = list(h.extract(inst))
    assert [(e.source, e.locator, e.original) for e in entries] == [
        ("config/tips/tips.txt", "0:0", "Use a §aWrench§r to rotate."),
        ("config/tips/tips.txt", "0:1", "Press F3 for debug info."),
    ]
    sink = OutputSink(instance_dir)
    h.write([TranslatedItem(entries[1], "按 F3 查看调试信息。", "llm", "done")], sink)
    assert sink.instance_files["config/tips/tips.txt"].decode("utf-8") == TIPS.replace(
        "Press F3 for debug info.", "按 F3 查看调试信息。")


def test_no_rules_no_detect(instance_dir):
    assert not ConfigTextHandler().detect(load_instance(instance_dir))
