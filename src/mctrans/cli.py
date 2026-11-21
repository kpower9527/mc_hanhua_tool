from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from .config import ConfigError, home_dir, load_settings
from .handlers import all_handlers
from .instance import InstanceError, load_instance
from .output.apply import ApplyError, apply as apply_outputs, restore as restore_outputs
from .store import Workspace
from .translate.client import client_from_config
from .translate.engine import EngineSettings, estimate, run_translation
from .translate.glossary import build_glossary, load_user_glossary

app = typer.Typer(help="Minecraft 整合包自动汉化工具", no_args_is_help=True, add_completion=False)
console = Console()

MAX_WARNINGS_SHOWN = 20

InstanceArg = Annotated[
    Path, typer.Argument(help="游戏实例目录（含 mods/ 的那一层）", exists=True, file_okay=False, resolve_path=True)
]
ProviderOpt = Annotated[Optional[str], typer.Option("--provider", "-p", help="翻译服务：deepseek / qwen / 自定义")]


def _fail(message: str) -> None:
    console.print(f"[red]错误：[/red]{message}")
    raise typer.Exit(1)


def _open_scanned(root: Path) -> Workspace:
    ws = Workspace.for_instance(root)
    if ws.get_meta("mc_version") is None:
        ws.close()
        _fail("这个实例还没有扫描过，请先运行 mctrans scan")
    return ws


def _write_log(root: Path, name: str, lines: list[str]) -> Path:
    log_dir = root / ".mctrans" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def do_scan(root: Path, mc_version: str | None) -> None:
    try:
        inst = load_instance(root, mc_version)
    except InstanceError as exc:
        _fail(str(exc))
    flags = " · 检测到 I18nUpdateMod" if inst.has_i18nupdatemod else ""
    console.print(f"Minecraft {inst.mc_version} · 加载器 {inst.loader} · {len(inst.mod_jars)} 个 mod{flags}")

    table = Table("处理器", "新增", "变更", "未变", "过时")
    warnings: list[str] = []
    with Workspace.for_instance(inst.root) as ws:
        ws.set_meta("mc_version", inst.mc_version)
        ws.set_meta("loader", inst.loader)
        ws.set_meta("has_i18nupdatemod", "1" if inst.has_i18nupdatemod else "0")
        run_id = ws.start_run("scan", {"mc_version": inst.mc_version})
        totals: dict[str, int] = {}
        for handler in all_handlers():
            if not handler.detect(inst):
                continue
            s = ws.sync_entries(handler.name, handler.extract(inst))
            table.add_row(handler.name, str(s.added), str(s.changed), str(s.unchanged), str(s.stale))
            warnings.extend(handler.warnings)
            totals[handler.name] = s.added + s.changed + s.unchanged
        builtin = ws.apply_builtin()
        ws.finish_run(run_id, {"entries": totals, "builtin": builtin, "warnings": len(warnings)})
    console.print(table)
    console.print(f"采用 mod 自带中文：{builtin} 条")
    if warnings:
        for w in warnings[:MAX_WARNINGS_SHOWN]:
            console.print(f"[yellow]警告：[/yellow]{w}")
        log = _write_log(inst.root, "scan", warnings)
        console.print(f"共 {len(warnings)} 条警告，完整列表见 {log}")


def do_translate(root: Path, provider: str | None, dry_run: bool, yes: bool) -> None:
    try:
        settings = load_settings()
        pcfg = settings.provider(provider)
    except ConfigError as exc:
        _fail(str(exc))
    engine_settings = EngineSettings.from_settings(settings)
    ws = _open_scanned(root)
    with ws:
        ws.apply_builtin()
        entries = ws.pending_entries()
        est = estimate(entries, engine_settings, pcfg.price_input, pcfg.price_output)
        console.print(
            f"待翻译 {est.entries} 条（去重后 {est.units} 条），预计 {est.requests} 次请求，"
            f"输入约 {est.input_tokens:,} tokens、输出约 {est.output_tokens:,} tokens，"
            f"使用 {pcfg.name}/{pcfg.model} 预计费用约 ¥{est.cost:.2f}（按配置单价估算，以官网为准）"
        )
        if dry_run or est.entries == 0:
            return
        try:
            client = client_from_config(pcfg)  # 先检查 API Key，再请求确认
        except ConfigError as exc:
            _fail(str(exc))
        if not yes and not typer.confirm("开始翻译吗？", default=True):
            raise typer.Exit(0)
        glossary = build_glossary(ws.glossary_pairs(), load_user_glossary(home_dir() / "glossary.tsv"))
        run_id = ws.start_run("translate", {"provider": pcfg.name, "model": pcfg.model})
        with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                      TimeRemainingColumn(), console=console) as progress:
            task = progress.add_task("翻译中", total=est.entries)
            report = run_translation(ws, client, glossary, engine_settings,
                                     on_progress=lambda n: progress.advance(task, n))
        ws.finish_run(run_id, vars(report))
    console.print(
        f"完成：翻译 {report.translated} 条，失败 {report.failed} 条，"
        f"跳过（无需翻译）{report.skipped} 条，共 {report.requests} 次请求"
    )
    if report.failed:
        console.print("[yellow]有条目翻译失败，再次运行 translate 会只重试失败的条目[/yellow]")


def do_apply(root: Path, set_lang: bool, dry_run: bool) -> None:
    ws = _open_scanned(root)
    with ws:
        report = apply_outputs(root, ws, ws.get_meta("mc_version"), set_lang=set_lang, dry_run=dry_run)
    prefix = "（预演，未写入）" if dry_run else ""
    console.print(f"{prefix}写入译文 {report.entries} 条")
    for name, n in report.packs.items():
        console.print(f"资源包：resourcepacks/{name}（{n} 个文件）")
    for rel in report.instance_files:
        console.print(f"修改文件：{rel}")
    if not dry_run:
        console.print("已自动备份，可用 mctrans restore 一键还原")


@app.command()
def scan(instance: InstanceArg,
         mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None):
    """扫描实例，提取待翻译文本。"""
    do_scan(instance, mc_version)


@app.command()
def translate(instance: InstanceArg, provider: ProviderOpt = None,
              dry_run: Annotated[bool, typer.Option("--dry-run", help="只预估费用，不调用 API")] = False,
              yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认")] = False):
    """翻译待处理的条目（可中断，再次运行会续跑）。"""
    do_translate(instance, provider, dry_run, yes)


@app.command("apply")
def apply_cmd(instance: InstanceArg,
              set_lang: Annotated[bool, typer.Option("--set-lang", help="同时把游戏语言设为简体中文")] = False,
              dry_run: Annotated[bool, typer.Option("--dry-run", help="只显示将要写入的内容")] = False):
    """把译文写入实例（生成资源包并启用，修改前自动备份）。"""
    do_apply(instance, set_lang, dry_run)


@app.command("restore")
def restore_cmd(instance: InstanceArg):
    """撤销 apply 的所有修改。"""
    try:
        report = restore_outputs(instance)
    except ApplyError as exc:
        _fail(str(exc))
    console.print(f"已还原 {len(report.restored)} 个文件，删除 {len(report.removed)} 个生成文件")
    for rel in report.skipped:
        console.print(f"[yellow]跳过（应用后被改动过）：[/yellow]{rel}")


@app.command()
def status(instance: InstanceArg):
    """查看翻译进度。"""
    ws = _open_scanned(instance)
    with ws:
        console.print(f"Minecraft {ws.get_meta('mc_version')} · 加载器 {ws.get_meta('loader')}")
        table = Table("状态", "条目数")
        for name, n in sorted(ws.status_counts().items()):
            table.add_row(name, str(n))
    console.print(table)


@app.command()
def run(instance: InstanceArg,
        mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None,
        provider: ProviderOpt = None,
        set_lang: Annotated[bool, typer.Option("--set-lang", help="同时把游戏语言设为简体中文")] = False,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认")] = False):
    """一键执行：扫描 → 翻译 → 应用。"""
    do_scan(instance, mc_version)
    do_translate(instance, provider, dry_run=False, yes=yes)
    do_apply(instance, set_lang, dry_run=False)
