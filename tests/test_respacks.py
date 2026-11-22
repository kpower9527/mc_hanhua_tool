import io
import json
import zipfile

from helpers import jar_bytes, lang
from mctrans import service
from mctrans.output.options import read_packs
from mctrans.respacks import PatchIndex, installed_patches

from test_service import Recorder, pack  # noqa: F401  复用 test_service 的整合包夹具


def write_patch(root, name, files):
    path = root / "resourcepacks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jar_bytes(files))
    return path


def set_packs(root, packs):
    (root / "options.txt").write_text(f"resourcePacks:{json.dumps(packs)}\n", encoding="utf-8")


def test_installed_patches_order_and_filters(instance_dir):
    write_patch(instance_dir, "A.zip", {"assets/x/lang/zh_cn.json": lang({"k": "甲", "only_a": "仅甲"})})
    write_patch(instance_dir, "B.zip", {"assets/x/lang/zh_cn.json": lang({"k": "乙"})})
    write_patch(instance_dir, "C.zip", {"assets/x/lang/zh_cn.json": lang({"k": "丙"})})  # 未启用
    write_patch(instance_dir, "textures.zip", {"assets/x/textures/a.png": b"png"})       # 无中文：忽略
    write_patch(instance_dir, "maikuai-zh_cn.zip", {"assets/x/lang/zh_cn.json": lang({"k": "我们"})})
    folder = instance_dir / "resourcepacks" / "D"
    (folder / "assets" / "y" / "lang").mkdir(parents=True)
    (folder / "pack.mcmeta").write_text("{}", encoding="utf-8")
    (folder / "assets" / "y" / "lang" / "zh_cn.json").write_text(lang({"t": "丁"}), encoding="utf-8")
    # 列表越靠后优先级越高：B 在最上面
    set_packs(instance_dir, ["vanilla", "file/A.zip", "file/D", "file/B.zip"])

    patches = installed_patches(instance_dir)
    assert [p.name for p in patches] == ["B.zip", "D", "A.zip"]  # C 未启用，不采用
    index = PatchIndex(patches)
    assert index.lookup("x", "k") == "乙"
    assert index.lookup("x", "only_a") == "仅甲"
    assert index.lookup("y", "t") == "丁"


def test_scan_absorbs_patch_and_apply_moves_ours_to_top(pack):
    root, fake = pack
    service.scan(root, reporter=Recorder())
    service.translate(root, reporter=Recorder())
    service.apply(root)
    assert service.packs_above_ours(root) == []

    # 纹理包、其他语言的翻译包、模组内置资源包排在上面不影响中文，不提示
    write_patch(root, "textures.zip", {"assets/create/textures/a.png": b"png"})
    write_patch(root, "es.zip", {"assets/create/lang/es_es.json": lang({"tip.create.y": "Cinta"})})
    text = (root / "options.txt").read_text(encoding="utf-8")
    set_packs(root, read_packs(text) + ["file/textures.zip", "file/es.zip", "prominent:archon"])
    assert service.packs_above_ours(root) == []

    # 之后玩家装了一个汉化补丁，安装程序把它放到了最上面
    write_patch(root, "整合包汉化补丁.zip", {
        "assets/create/lang/zh_cn.json": lang({"tip.create.y": "传送带", "tip.create.x": "参数不对"}),
        "assets/create/patchouli_books/guide/zh_cn/entries/a.json": "{}",
    })
    text = (root / "options.txt").read_text(encoding="utf-8")
    set_packs(root, read_packs(text) + ["file/整合包汉化补丁.zip"])
    assert service.packs_above_ours(root) == ["整合包汉化补丁.zip"]

    rec = Recorder()
    service.reapply_on_top(root, reporter=rec)
    assert any("整合包汉化补丁.zip" in t for _, t in rec.logs)
    assert service.summary(root).origins.get("respack") == 1  # 参数不一致的那条不采用
    packs = read_packs((root / "options.txt").read_text(encoding="utf-8"))
    assert packs[-1] == "file/maikuai-zh_cn.zip"
    assert service.packs_above_ours(root) == []
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes())) as z:
        data = json.loads(z.read("assets/create/lang/zh_cn.json"))
    assert data["tip.create.y"] == "传送带"
    assert data["tip.create.x"] == "译Use the Gear %s"

    service.restore(root)
    assert (root / "resourcepacks" / "整合包汉化补丁.zip").exists()


def test_apply_skips_whole_files_provided_by_patch(instance_dir):
    from mctrans.handlers.base import OutputSink
    from mctrans.output.apply import Outputs, skip_patch_files

    write_patch(instance_dir, "p.zip", {
        "assets/a/lang/zh_cn.json": lang({"k": "补丁"}),
        "assets/a/patchouli_books/b/zh_cn/entries/E.json": "{}",
    })
    sink = OutputSink()
    sink.add_pack_file("assets/a/lang/zh_cn.json", b"{}")
    sink.add_pack_file("assets/a/patchouli_books/b/zh_cn/entries/e.json", b"{}")
    sink.add_pack_file("assets/a/patchouli_books/b/zh_cn/entries/f.json", b"{}")
    only = OutputSink()
    only.add_pack_file("assets/a/patchouli_books/b/zh_cn/entries/e.json", b"{}")
    outputs = Outputs({"x.zip": sink, "y.zip": only}, OutputSink(), 3)
    skip_patch_files(outputs, instance_dir)
    assert sorted(outputs.packs["x.zip"].pack_files) == [
        "assets/a/lang/zh_cn.json", "assets/a/patchouli_books/b/zh_cn/entries/f.json"]
    assert "y.zip" not in outputs.packs


def test_lang_extracted_from_enabled_and_paxi_packs(instance_dir):
    from mctrans.handlers.lang_json import LangJsonHandler
    from mctrans.instance import load_instance

    make = __import__("helpers").make_jar
    make(instance_dir / "mods" / "spell.jar", {
        "META-INF/mods.toml": 'modId="spell_power"',
        "assets/spell_power/lang/en_us.json": lang({"attr.arcane": "Arcane Power", "attr.fire": "Fire Power"}),
        "assets/spell_power/lang/zh_cn.json": lang({"attr.arcane": "奥术强度", "attr.fire": "火焰强度"}),
    })
    paxi = instance_dir / "config" / "paxi" / "resourcepacks"
    paxi.mkdir(parents=True)
    (paxi / "Overrides.zip").write_bytes(jar_bytes({
        "assets/a_overrides/lang/en_us.json": lang({"attr.arcane": "§f\ue001§r Arcane Power"})}))
    write_patch(instance_dir, "Tooltips.zip", {
        "assets/tips/lang/en_us.json": lang({"tip.x": "Drops from bosses."}),
        "assets/spell_power/lang/en_us.json": lang({"attr.fire": "Fire Power"}),       # 与模组相同
        "assets/minecraft/lang/en_us.json": lang({"block.minecraft.stone": "Rock"})})  # 原版：不翻译
    write_patch(instance_dir, "Disabled.zip", {
        "assets/off/lang/en_us.json": lang({"tip.off": "Not loaded"})})
    set_packs(instance_dir, ["vanilla", "file/Tooltips.zip", "Overrides.zip"])

    entries = {e.locator: e for e in LangJsonHandler().extract(load_instance(instance_dir))}
    assert entries["tip.x"].source == "pack:Tooltips.zip!assets/tips/lang/en_us.json"
    assert entries["attr.arcane"].original == "§f\ue001§r Arcane Power"  # 资源包改写的英文为准
    assert entries["attr.arcane"].existing_zh is None
    assert entries["attr.fire"].existing_zh == "火焰强度" and entries["attr.fire"].source.startswith("jar:")
    assert "block.minecraft.stone" not in entries
    assert "tip.off" not in entries


def test_glyphs_protected_only_for_model():
    from mctrans.translate.protect import protect

    masked = protect("§f陋§r Arcane \ue001Power", glyphs=True)
    assert masked.tokens == ["§f", "陋", "§r", "\ue001"]
    assert protect("§f陋§r Arcane", glyphs=False).tokens == ["§f", "§r"]
