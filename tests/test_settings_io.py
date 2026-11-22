from mctrans.config import ProviderConfig, load_settings
from mctrans.handlers.config_text import parse_rules
from mctrans.instances import add_instance, load_instances, remove_instance
from mctrans.settings_io import save_settings, save_user_glossary
from mctrans.translate.glossary import load_user_glossary


def test_settings_round_trip(mctrans_home):
    s = load_settings()
    s.default_provider = "qwen"
    s.concurrency = 8
    s.cfpa_base_url = "https://mirror.example/cfpa"
    s.cfpa_check_hours = 1.5
    s.patch_extras_dir = 'D:\\广告'
    s.providers["qwen"].api_key = 'sk-"quote"\\back'
    s.providers["qwen"].model = "qwen3.7-flash"
    s.providers["local"] = ProviderConfig("local", "http://localhost:11434/v1", "模型", api_key="x", price_input=0.5)
    save_settings(s)
    t = load_settings()
    assert t.default_provider == "qwen" and t.concurrency == 8 and t.cfpa_check_hours == 1.5 and t.patch_extras_dir == 'D:\\广告'
    assert t.cfpa_base_url == "https://mirror.example/cfpa"
    assert t.providers["qwen"].api_key == 'sk-"quote"\\back' and t.providers["qwen"].model == "qwen3.7-flash"
    assert t.providers["local"].model == "模型" and t.providers["local"].price_input == 0.5
    assert t.providers["deepseek"].base_url == "https://api.deepseek.com"


def test_glossary_round_trip(tmp_path):
    path = tmp_path / "glossary.tsv"
    pairs = [("Andesite Alloy", "安山合金"), ("Gear", "齿轮")]
    save_user_glossary(path, pairs + [("", "空"), ("Tab\tin", "x")])
    assert load_user_glossary(path) == pairs


def test_parse_rules():
    rules, warnings = parse_rules('[[rule]]\nglob = "config/*.txt"\npattern = \'^tip=(?P<text>.+)$\'\n', "规则")
    assert [r.glob for r in rules] == ["config/*.txt"] and warnings == []
    rules, warnings = parse_rules('[[rule]]\nglob = "x"\npattern = "no group"\n', "规则")
    assert rules == [] and "text" in warnings[0]
    rules, warnings = parse_rules("not toml [", "规则")
    assert rules == [] and "格式错误" in warnings[0]


def test_instances(mctrans_home, tmp_path):
    a, b = tmp_path / "PackA", tmp_path / "PackB"
    assert load_instances() == []
    add_instance(a)
    records = add_instance(b)
    assert [r.name for r in records] == ["PackB", "PackA"]
    records = add_instance(a)  # 重新添加：移到最前，不重复
    assert [r.name for r in records] == ["PackA", "PackB"]
    assert [r.name for r in remove_instance(b)] == ["PackA"]
    assert [r.path for r in load_instances()] == [str(a.resolve())]
