from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..store import Workspace
from . import apply as apply_mod
from .resourcepack import build_pack_zip, pack_mcmeta

README_NAME = "汉化补丁说明.txt"
CFPA_NOTICE = """
本补丁包含来自 CFPA（Minecraft 模组简体中文翻译项目，https://github.com/CFPAOrg/Minecraft-Mod-Language-Package）
的译文，这部分内容采用 知识共享 署名-非商业性使用-相同方式共享 4.0 国际许可协议（CC BY-NC-SA 4.0）授权：
https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans
分发本补丁时请保留本说明，且不得用于商业用途。
"""


@dataclass
class PatchReport:
    entries: int
    files: list[str]
    includes_cfpa: bool


def _readme(mc_version: str, mode: str, packs: list[str], instance_files: list[str], includes_cfpa: bool) -> str:
    lines = [
        "Minecraft 整合包汉化补丁",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}    Minecraft 版本：{mc_version}",
        "生成工具：mctrans（大模型机翻 + 社区译文，可能存在错译，欢迎反馈修正）",
        "",
        "使用方法：",
        "1. 把本目录中除本说明外的所有文件，按相同的目录结构复制到整合包的实例目录（包含 mods 文件夹的那一层），覆盖同名文件。",
    ]
    if packs:
        if mode == apply_mod.MODE_I18N:
            lines.append("2. 进入游戏，在「选项 → 资源包」中启用以下资源包："
                         "补全包（mctrans-zh_cn-fill.zip）放在 I18nUpdateMod 的汉化资源包下方，"
                         "修正包（mctrans-zh_cn-fix.zip，如有）放在其上方。")
        else:
            lines.append("2. 进入游戏，在「选项 → 资源包」中启用 mctrans-zh_cn.zip，并放在最上方。")
        lines.append("3. 在「选项 → 语言」中选择简体中文。")
    lines += ["", "包含的文件："]
    lines += [f"  resourcepacks/{name}" for name in packs]
    lines += [f"  {rel}（覆盖整合包中的同名文件，建议先备份）" for rel in instance_files]
    if includes_cfpa:
        lines.append(CFPA_NOTICE)
    return "\n".join(lines) + "\n"


def export_patch(root: Path, ws: Workspace, mc_version: str, out_dir: Path, mode: str) -> PatchReport:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise apply_mod.ApplyError(f"输出目录不为空：{out_dir}")
    outputs = apply_mod.build_outputs(ws, mode, Path(root))
    files: list[str] = []
    for rel, data in sorted(outputs.instance.instance_files.items()):
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(rel)
    pack_names = sorted(outputs.packs)
    for name in pack_names:
        target = out_dir / "resourcepacks" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_pack_zip(outputs.packs[name].pack_files,
                                          pack_mcmeta(mc_version, apply_mod.PACK_DESCRIPTION)))
        files.append(f"resourcepacks/{name}")
    includes_cfpa = mode == apply_mod.MODE_STANDALONE and ws.origin_counts().get("cfpa", 0) > 0
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / README_NAME).write_text(
        _readme(mc_version, mode, pack_names, sorted(outputs.instance.instance_files), includes_cfpa),
        encoding="utf-8")
    return PatchReport(outputs.entries, sorted(files) + [README_NAME], includes_cfpa)
