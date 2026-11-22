from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from . import service
from .config import ConfigError
from .service import Reporter, ServiceError

app = typer.Typer(help="Minecraft 整合包自动汉化工具", no_args_is_help=True, add_completion=False)
review_app = typer.Typer(help="导出 / 导入 Excel 审校表格", no_args_is_help=True)
app.add_typer(review_app, name="review")
console = Console()

MAX_WARNINGS_SHOWN = 20


def main() -> None:
    """命令行入口。中文 Windows 控制台默认为 GBK，遇到无法编码的字符时替换为 ? 而不是崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    app(prog_name="maikuai-cli")

InstanceArg = Annotated[
    Path, typer.Argument(help="游戏实例目录（含 mods/ 的那一层）", exists=True, file_okay=False, resolve_path=True)
]
ProviderOpt = Annotated[Optional[str], typer.Option("--provider", "-p", help="翻译服务：deepseek / qwen / 自定义")]
ModeOpt = Annotated[str, typer.Option("--mode", help="输出模式：auto / standalone / i18nupdate")]
OnlyOpt = Annotated[Optional[list[str]], typer.Option(
    "--only", help="只翻译指定类别，可重复：lang_json / ftbquests / patchouli / betterquesting / kubejs / crafttweaker / structures / config_text")]
LimitOpt = Annotated[Optional[int], typer.Option("--limit", help="最多翻译多少条（抽样试翻、控制费用）")]


class ConsoleReporter(Reporter):
    """把日志打印到控制台；有进度时显示进度条。"""

    def __init__(self, progress_label: str | None = None) -> None:
        self.progress_label = progress_label
        self._progress: Progress | None = None
        self._task = None
        self._warnings = 0

    def log(self, text: str, level: str = "info") -> None:
        if level == "warn":
            self._warnings += 1
            if self._warnings > MAX_WARNINGS_SHOWN:
                return
            text = f"[yellow]警告：[/yellow]{text}"
        elif level == "error":
            text = f"[red]错误：[/red]{text}"
        console.print(text)

    def progress(self, done: int, total: int) -> None:
        if self.progress_label is None:
            return
        if self._progress is None:
            self._progress = Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                                      TimeRemainingColumn(), console=console)
            self._progress.start()
            self._task = self._progress.add_task(self.progress_label, total=total)
        self._progress.update(self._task, completed=done, total=total)

    def close(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None


def _fail(message: str) -> None:
    console.print(f"[red]错误：[/red]{message}")
    raise typer.Exit(1)


def do_scan(root: Path, mc_version: str | None, use_cfpa: bool = True, offline: bool = False) -> None:
    try:
        result = service.scan(root, mc_version, use_cfpa=use_cfpa, offline=offline, reporter=ConsoleReporter())
    except ServiceError as exc:
        _fail(str(exc))
    table = Table("处理器", "新增", "变更", "未变", "过时")
    for s in result.handlers:
        table.add_row(s.name, str(s.added), str(s.changed), str(s.unchanged), str(s.stale))
    console.print(table)


def do_translate(root: Path, provider: str | None, dry_run: bool, yes: bool,
                 only: list[str] | None = None, limit: int | None = None) -> None:
    try:
        plan = service.plan_translation(root, provider, only, limit)
    except ServiceError as exc:
        _fail(str(exc))
    console.print(service.describe_estimate(plan.estimate, plan.provider))
    if dry_run or plan.estimate.entries == 0:
        return
    try:
        service.client_from_config(plan.provider)  # 先检查 API Key，再请求确认
    except ConfigError as exc:
        _fail(str(exc))
    if not yes and not typer.confirm("开始翻译吗？", default=True):
        raise typer.Exit(0)
    reporter = ConsoleReporter("翻译中")
    try:
        service.translate(root, provider, only, limit, reporter=reporter, announce=False)
    except ServiceError as exc:
        _fail(str(exc))
    finally:
        reporter.close()


def do_apply(root: Path, set_lang: bool, dry_run: bool, mode: str = "auto") -> None:
    try:
        report = service.apply(root, mode=mode, set_lang=set_lang, dry_run=dry_run)
    except ServiceError as exc:
        _fail(str(exc))
    prefix = "（预演，未写入）" if dry_run else ""
    mode_label = "配合 I18nUpdateMod" if report.mode == "i18nupdate" else "独立"
    console.print(f"{prefix}输出模式：{report.mode}（{mode_label}），写入译文 {report.entries} 条")
    for name, n in report.packs.items():
        console.print(f"资源包：resourcepacks/{name}（{n} 个文件）")
    for rel in report.instance_files:
        console.print(f"修改文件：{rel}")
    if not dry_run:
        console.print("已自动备份，可用 maikuai-cli restore 一键还原")


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
        report = service.restore(instance)
    except ServiceError as exc:
        _fail(str(exc))
    console.print(f"已还原 {len(report.restored)} 个文件，删除 {len(report.removed)} 个生成文件")
    for rel in report.skipped:
        console.print(f"[yellow]跳过（应用后被改动过）：[/yellow]{rel}")


@app.command()
def status(instance: InstanceArg):
    """查看翻译进度。"""
    s = service.summary(instance)
    if not s.scanned:
        _fail("这个整合包还没有扫描过，请先运行 maikuai-cli scan")
    console.print(f"Minecraft {s.mc_version} · 加载器 {s.loader}")
    totals: dict[str, int] = {}
    for counts in s.by_handler.values():
        for name, n in counts.items():
            totals[name] = totals.get(name, 0) + n
    table = Table("状态", "条目数")
    for name, n in sorted(totals.items()):
        table.add_row(name, str(n))
    origins = Table("译文来源", "条目数")
    for name, n in sorted(s.origins.items()):
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
    try:
        n = service.review_export(instance, file, include_all=all_rows, handlers=only)
    except ServiceError as exc:
        _fail(str(exc))
    console.print(f"已导出 {n} 条到 {file}")
    console.print("修改「译文」列或在「确认」列填「是」，保存后运行 maikuai-cli review import 导入")


@review_app.command("import")
def review_import(instance: InstanceArg,
                  file: Annotated[Path, typer.Argument(help="审校后的 Excel 文件", exists=True, dir_okay=False)],
                  force: Annotated[bool, typer.Option("--force", help="不检查格式码是否与原文一致")] = False):
    """导入审校结果（标记为人工确认，并写入记忆库）。"""
    try:
        report = service.review_import(instance, file, force=force)
    except ServiceError as exc:
        _fail(str(exc))
    console.print(
        f"导入完成：修改 {report.updated} 条，确认 {report.confirmed} 条，未改动 {report.unchanged} 条，"
        f"找不到的 ID {report.unknown} 条，拒绝 {len(report.invalid)} 条"
    )
    for line in report.invalid[:MAX_WARNINGS_SHOWN]:
        console.print(f"[yellow]拒绝：[/yellow]{line}")
    if report.updated or report.confirmed:
        console.print("运行 maikuai-cli apply 使修改生效")


@app.command("export-patch")
def export_patch_cmd(instance: InstanceArg,
                     out_dir: Annotated[Path, typer.Argument(help="补丁输出目录（必须不存在或为空）")],
                     mode: ModeOpt = "auto"):
    """把汉化结果导出为独立的补丁目录，便于分发（不修改实例）。"""
    try:
        report = service.export_patch(instance, out_dir, mode)
    except ServiceError as exc:
        _fail(str(exc))
    console.print(f"已导出补丁到 {out_dir}（译文 {report.entries} 条，文件 {len(report.files)} 个）")
    if report.includes_cfpa:
        console.print("[yellow]补丁包含 CFPA 社区译文（CC BY-NC-SA 4.0），分发时请保留说明文件，且不得商用[/yellow]")
