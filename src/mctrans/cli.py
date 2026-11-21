from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from .cfpa.cache import CfpaCache, http_fetch
from .cfpa.index import CfpaIndex
from .cfpa.versions import cfpa_assets
from .config import ConfigError, home_dir, load_settings
from .handlers import all_handlers
from .instance import InstanceError, load_instance
from .match import MatchStats, remember_translations, run_matching
from .memory import Memory
from .output.apply import ApplyError, apply as apply_outputs, resolve_mode, restore as restore_outputs
from .output.patch import export_patch
from .review import export_review, import_review
from .store import Workspace
from .translate.client import client_from_config
from .translate.engine import EngineSettings, estimate, run_translation, select_entries
from .translate.glossary import build_glossary, load_user_glossary

app = typer.Typer(help="Minecraft 整合包自动汉化工具", no_args_is_help=True, add_completion=False)
review_app = typer.Typer(help="导出 / 导入 Excel 审校表格", no_args_is_help=True)
app.add_typer(review_app, name="review")
console = Console()

MAX_WARNINGS_SHOWN = 20

InstanceArg = Annotated[
    Path, typer.Argument(help="游戏实例目录（含 mods/ 的那一层）", exists=True, file_okay=False, resolve_path=True)
]
ProviderOpt = Annotated[Optional[str], typer.Option("--provider", "-p", help="翻译服务：deepseek / qwen / 自定义")]
ModeOpt = Annotated[str, typer.Option("--mode", help="输出模式：auto / standalone / i18nupdate")]
OnlyOpt = Annotated[Optional[list[str]], typer.Option(
    "--only", help="只翻译指定类别，可重复：lang_json / ftbquests / patchouli / betterquesting / kubejs / crafttweaker / config_text")]
LimitOpt = Annotated[Optional[int], typer.Option("--limit", help="最多翻译多少条（抽样试翻、控制费用）")]


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


def _load_cfpa(inst, settings, offline: bool) -> CfpaIndex | None:
    assets = cfpa_assets(inst.mc_version, inst.loader)
    if not assets:
        console.print(f"CFPA 暂不支持 Minecraft {inst.mc_version}，跳过")
        return None
    cache = CfpaCache(home_dir() / "cfpa", settings.cfpa_base_url, fetch=http_fetch,
                      check_hours=settings.cfpa_check_hours)
    zips: list[bytes] = []
    for asset in assets:
        result = cache.get(asset, offline=offline)
        label = {"updated": "已下载最新版", "fresh": "已是最新", "cached": "使用缓存", "missing": "不可用"}[result.status]
        console.print(f"CFPA 汉化包 {asset.key}：{label}")
        if result.message:
            console.print(f"[yellow]警告：[/yellow]{result.message}")
        if result.data is not None:
            zips.append(result.data)
    if not zips:
        console.print("[yellow]没有可用的 CFPA 汉化包，本次只使用 mod 自带中文和记忆库[/yellow]")
        return None
    index = CfpaIndex.from_zips(zips)
    for w in index.warnings[:MAX_WARNINGS_SHOWN]:
        console.print(f"[yellow]警告：[/yellow]{w}")
    return index


def _print_match(stats: MatchStats) -> None:
    console.print(
        f"采用 mod 自带中文 {stats.builtin} 条，CFPA 人工译文 {stats.cfpa} 条"
        f"（参数不一致、视为过时而跳过 {stats.cfpa_rejected} 条），"
        f"记忆库 {stats.memory} 条，记忆库中的人工译文 {stats.human} 条"
    )


def do_scan(root: Path, mc_version: str | None, use_cfpa: bool = True, offline: bool = False) -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        _fail(str(exc))
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
        index = _load_cfpa(inst, settings, offline) if use_cfpa else None
        with Memory.default() as memory:
            stats = run_matching(ws, index, memory)
        ws.finish_run(run_id, {"entries": totals, "match": vars(stats), "warnings": len(warnings)})
    console.print(table)
    _print_match(stats)
    if warnings:
        for w in warnings[:MAX_WARNINGS_SHOWN]:
            console.print(f"[yellow]警告：[/yellow]{w}")
        log = _write_log(inst.root, "scan", warnings)
        console.print(f"共 {len(warnings)} 条警告，完整列表见 {log}")


def do_translate(root: Path, provider: str | None, dry_run: bool, yes: bool,
                 only: list[str] | None = None, limit: int | None = None) -> None:
    try:
        settings = load_settings()
        pcfg = settings.provider(provider)
    except ConfigError as exc:
        _fail(str(exc))
    engine_settings = EngineSettings.from_settings(settings)
    ws = _open_scanned(root)
    with ws:
        with Memory.default() as memory:
            run_matching(ws, None, memory)
        entries = select_entries(ws.pending_entries(), set(only) if only else None, limit)
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
                                     on_progress=lambda n: progress.advance(task, n), entries=entries)
        ws.finish_run(run_id, vars(report))
        with Memory.default() as memory:
            remember_translations(ws, memory)
    console.print(
        f"完成：翻译 {report.translated} 条，失败 {report.failed} 条，"
        f"跳过（无需翻译）{report.skipped} 条，共 {report.requests} 次请求"
    )
    if report.failed:
        console.print("[yellow]有条目翻译失败，再次运行 translate 会只重试失败的条目[/yellow]")


def do_apply(root: Path, set_lang: bool, dry_run: bool, mode: str = "auto") -> None:
    ws = _open_scanned(root)
    with ws:
        try:
            chosen = resolve_mode(mode, ws.get_meta("has_i18nupdatemod") == "1")
        except ApplyError as exc:
            _fail(str(exc))
        report = apply_outputs(root, ws, ws.get_meta("mc_version"), mode=chosen,
                               set_lang=set_lang, dry_run=dry_run)
    prefix = "（预演，未写入）" if dry_run else ""
    mode_label = "配合 I18nUpdateMod" if report.mode == "i18nupdate" else "独立"
    console.print(f"{prefix}输出模式：{report.mode}（{mode_label}），写入译文 {report.entries} 条")
    for name, n in report.packs.items():
        console.print(f"资源包：resourcepacks/{name}（{n} 个文件）")
    for rel in report.instance_files:
        console.print(f"修改文件：{rel}")
    if not dry_run:
        console.print("已自动备份，可用 mctrans restore 一键还原")


@app.command()
def scan(instance: InstanceArg,
         mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None,
         no_cfpa: Annotated[bool, typer.Option("--no-cfpa", help="不使用 CFPA 汉化包")] = False,
         offline: Annotated[bool, typer.Option("--offline", help="不联网，只用本地缓存的 CFPA 汉化包")] = False):
    """扫描实例，提取待翻译文本并匹配已有译文。"""
    do_scan(instance, mc_version, use_cfpa=not no_cfpa, offline=offline)


@app.command()
def translate(instance: InstanceArg, provider: ProviderOpt = None,
              dry_run: Annotated[bool, typer.Option("--dry-run", help="只预估费用，不调用 API")] = False,
              yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认")] = False,
              only: OnlyOpt = None, limit: LimitOpt = None):
    """翻译待处理的条目（可中断，再次运行会续跑）。"""
    do_translate(instance, provider, dry_run, yes, only, limit)


@app.command("apply")
def apply_cmd(instance: InstanceArg,
              set_lang: Annotated[bool, typer.Option("--set-lang", help="同时把游戏语言设为简体中文")] = False,
              dry_run: Annotated[bool, typer.Option("--dry-run", help="只显示将要写入的内容")] = False,
              mode: ModeOpt = "auto"):
    """把译文写入实例（生成资源包并启用，修改前自动备份）。"""
    do_apply(instance, set_lang, dry_run, mode)


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
        origins = Table("译文来源", "条目数")
        for name, n in sorted(ws.origin_counts().items()):
            origins.add_row(name, str(n))
    console.print(table)
    console.print(origins)


@app.command()
def run(instance: InstanceArg,
        mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None,
        provider: ProviderOpt = None,
        set_lang: Annotated[bool, typer.Option("--set-lang", help="同时把游戏语言设为简体中文")] = False,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认")] = False,
        mode: ModeOpt = "auto", only: OnlyOpt = None, limit: LimitOpt = None):
    """一键执行：扫描 → 翻译 → 应用。"""
    do_scan(instance, mc_version)
    do_translate(instance, provider, dry_run=False, yes=yes, only=only, limit=limit)
    do_apply(instance, set_lang, dry_run=False, mode=mode)


@review_app.command("export")
def review_export(instance: InstanceArg,
                  file: Annotated[Path, typer.Argument(help="导出的 Excel 文件路径（.xlsx）")],
                  all_rows: Annotated[bool, typer.Option("--all", help="导出全部条目（默认只导出机翻、待翻译和需复核的）")] = False,
                  only: OnlyOpt = None):
    """导出待审校的条目到 Excel。"""
    ws = _open_scanned(instance)
    with ws:
        n = export_review(ws, file, include_all=all_rows, handlers=set(only) if only else None)
    console.print(f"已导出 {n} 条到 {file}")
    console.print("修改「译文」列或在「确认」列填「是」，保存后运行 mctrans review import 导入")


@review_app.command("import")
def review_import(instance: InstanceArg,
                  file: Annotated[Path, typer.Argument(help="审校后的 Excel 文件", exists=True, dir_okay=False)],
                  force: Annotated[bool, typer.Option("--force", help="不检查格式码是否与原文一致")] = False):
    """导入审校结果（标记为人工确认，并写入记忆库）。"""
    ws = _open_scanned(instance)
    with ws:
        try:
            report = import_review(ws, file, force=force)
        except ValueError as exc:
            _fail(str(exc))
        with Memory.default() as memory:
            remember_translations(ws, memory)
    console.print(
        f"导入完成：修改 {report.updated} 条，确认 {report.confirmed} 条，未改动 {report.unchanged} 条，"
        f"找不到的 ID {report.unknown} 条，拒绝 {len(report.invalid)} 条"
    )
    for line in report.invalid[:MAX_WARNINGS_SHOWN]:
        console.print(f"[yellow]拒绝：[/yellow]{line}")
    if report.updated or report.confirmed:
        console.print("运行 mctrans apply 使修改生效")


@app.command("export-patch")
def export_patch_cmd(instance: InstanceArg,
                     out_dir: Annotated[Path, typer.Argument(help="补丁输出目录（必须不存在或为空）")],
                     mode: ModeOpt = "auto"):
    """把汉化结果导出为独立的补丁目录，便于分发（不修改实例）。"""
    ws = _open_scanned(instance)
    with ws:
        try:
            chosen = resolve_mode(mode, ws.get_meta("has_i18nupdatemod") == "1")
            report = export_patch(instance, ws, ws.get_meta("mc_version"), out_dir, chosen)
        except ApplyError as exc:
            _fail(str(exc))
    console.print(f"已导出补丁到 {out_dir}（译文 {report.entries} 条，文件 {len(report.files)} 个）")
    if report.includes_cfpa:
        console.print("[yellow]补丁包含 CFPA 社区译文（CC BY-NC-SA 4.0），分发时请保留说明文件，且不得商用[/yellow]")
