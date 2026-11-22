from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..brand import APP_NAME, PACK_DESCRIPTION, SITE_URL
from ..store import Workspace
from . import apply as apply_mod
from .installer import BAT, BAT_NAME, PS1_NAME, install_script
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
        f"生成工具：{APP_NAME}（{SITE_URL}）",
        "译文来自 mod 自带中文、CFPA 社区汉化与大模型翻译，可能存在错译，欢迎反馈修正。",
        "",
        "一键安装（推荐，Windows）：",
        "  先关闭游戏，然后双击「" + BAT_NAME + "」，按提示选择整合包目录（包含 mods 文件夹的那一层）。",
        "  也可以把整合包文件夹直接拖到「" + BAT_NAME + "」上；如果补丁已解压到整合包目录里，直接双击即可。",
        "  脚本会复制补丁文件（被覆盖的原文件自动备份到 .hanhua-backup 文件夹），",
        "  把汉化资源包放到资源包列表最上面，并把游戏语言设为简体中文。",
        "",
        "手动安装：",
        "1. 把本目录中的文件（说明和安装脚本除外）按相同的目录结构复制到整合包目录，覆盖同名文件。",
    ]
    if packs:
        if mode == apply_mod.MODE_I18N:
            lines.append(f"2. 进入游戏，在「选项 → 资源包」中启用以下资源包："
                         f"补全包（{apply_mod.FILL_FILE}）放在 I18nUpdateMod 的汉化资源包下方，"
                         f"修正包（{apply_mod.FIX_FILE}，如有）放在其上方。")
        else:
            lines.append(f"2. 进入游戏，在「选项 → 资源包」中启用 {apply_mod.PACK_FILE}，并放在最上方"
                         "（排在其他汉化包下面时，重复的条目会以上面的为准）。")
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
                                          pack_mcmeta(mc_version, PACK_DESCRIPTION)))
        files.append(f"resourcepacks/{name}")
    includes_cfpa = mode == apply_mod.MODE_STANDALONE and ws.origin_counts().get("cfpa", 0) > 0
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / README_NAME).write_text(
        _readme(mc_version, mode, pack_names, sorted(outputs.instance.instance_files), includes_cfpa),
        encoding="utf-8")
    ids = {name: apply_mod.pack_id(mc_version, name) for name in pack_names}
    if mode == apply_mod.MODE_I18N:
        top, anchor = [], apply_mod.i18n_pack_id(mc_version)
        below = [ids[n] for n in pack_names if n == apply_mod.FILL_FILE]
        above = [ids[n] for n in pack_names if n == apply_mod.FIX_FILE]
    else:
        top, anchor, below, above = [ids[n] for n in pack_names], "", [], []
    script = install_script(top, anchor, below, above, apply_mod.default_packs(mc_version),
                            [README_NAME, BAT_NAME, PS1_NAME])
    (out_dir / PS1_NAME).write_bytes(b"\xef\xbb\xbf" + script.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    (out_dir / BAT_NAME).write_bytes(BAT.encode("ascii"))
    return PatchReport(outputs.entries, sorted(files) + [README_NAME, BAT_NAME, PS1_NAME], includes_cfpa)
