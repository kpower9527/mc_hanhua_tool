"""服务层：命令行与图形界面共用的业务流程。

每个函数自行打开并关闭数据库连接（可在任意线程调用），通过 Reporter 报告日志与进度，
出错时抛出 ServiceError（消息为中文，可直接展示给用户）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .cfpa.cache import CfpaCache, http_fetch
from .cfpa.index import CfpaIndex
from .cfpa.versions import cfpa_assets
from .config import ConfigError, ProviderConfig, Settings, home_dir, load_settings
from .handlers import all_handlers
from .instance import InstanceError, load_instance
from .match import MatchStats, remember_translations, run_matching
from .memory import Memory
from .models import Entry
from .output import apply as apply_mod
from .output import patch as patch_mod
from .respacks import PatchIndex, installed_patches
from .review import ImportReport, export_review, import_review
from .store import ReviewRow, TranslationUpdate, Workspace
from .translate.client import client_from_config
from .translate.engine import (
    EngineSettings, Estimate, TranslateReport, estimate, run_translation, select_entries,
)
from .translate.glossary import build_glossary, load_user_glossary
from .translate.prompt import SYSTEM_PROMPT, build_user_prompt, parse_response
from .translate.protect import has_translatable_text, protect

FINISHED_STATUSES = ("done", "confirmed", "skipped")
CFPA_LABELS = {"updated": "已下载最新版", "fresh": "已是最新", "cached": "使用缓存", "missing": "不可用"}


class ServiceError(Exception):
    pass


class Reporter:
    """进度与日志回调。默认实现不输出任何内容。"""

    def log(self, text: str, level: str = "info") -> None:  # level: info / warn / error
        pass

    def progress(self, done: int, total: int) -> None:
        pass

    def cancelled(self) -> bool:
        return False


@dataclass
class HandlerStat:
    name: str
    added: int
    changed: int
    unchanged: int
    stale: int


@dataclass
class ScanResult:
    mc_version: str
    loader: str
    mod_count: int
    has_i18nupdatemod: bool
    handlers: list[HandlerStat]
    match: MatchStats
    warnings: list[str]
    log_path: Path | None


@dataclass
class TranslationPlan:
    entries: list[Entry]
    estimate: Estimate
    provider: ProviderConfig


@dataclass
class Summary:
    scanned: bool
    mc_version: str | None = None
    loader: str | None = None
    has_i18nupdatemod: bool = False
    last_scan: str | None = None
    by_handler: dict[str, dict[str, int]] = field(default_factory=dict)
    origins: dict[str, int] = field(default_factory=dict)
    total: int = 0
    finished: int = 0


def _settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc


def _workspace(root: Path) -> Workspace:
    if not (Path(root) / ".mctrans" / "workspace.db").is_file():
        raise ServiceError("这个整合包还没有扫描过，请先扫描（命令行：mctrans scan）")
    ws = Workspace.for_instance(root)
    if ws.get_meta("mc_version") is None:
        ws.close()
        raise ServiceError("这个整合包还没有扫描过，请先扫描（命令行：mctrans scan）")
    return ws


def _write_log(root: Path, name: str, lines: list[str]) -> Path:
    log_dir = Path(root) / ".mctrans" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_cfpa(inst, settings: Settings, offline: bool, reporter: Reporter) -> CfpaIndex | None:
    assets = cfpa_assets(inst.mc_version, inst.loader)
    if not assets:
        reporter.log(f"CFPA 暂不支持 Minecraft {inst.mc_version}，跳过")
        return None
    cache = CfpaCache(home_dir() / "cfpa", settings.cfpa_base_url, fetch=http_fetch,
                      check_hours=settings.cfpa_check_hours)
    zips: list[bytes] = []
    for asset in assets:
        result = cache.get(asset, offline=offline)
        reporter.log(f"CFPA 汉化包 {asset.key}：{CFPA_LABELS[result.status]}")
        if result.message:
            reporter.log(result.message, "warn")
        if result.data is not None:
            zips.append(result.data)
    if not zips:
        reporter.log("没有可用的 CFPA 汉化包，本次只使用 mod 自带中文和记忆库", "warn")
        return None
    index = CfpaIndex.from_zips(zips)
    for w in index.warnings:
        reporter.log(w, "warn")
    return index


def scan(root: Path, mc_version: str | None = None, use_cfpa: bool = True, offline: bool = False,
         reporter: Reporter | None = None) -> ScanResult:
    reporter = reporter or Reporter()
    settings = _settings()
    try:
        inst = load_instance(root, mc_version)
    except InstanceError as exc:
        raise ServiceError(str(exc)) from exc
    flags = " · 检测到 I18nUpdateMod" if inst.has_i18nupdatemod else ""
    reporter.log(f"Minecraft {inst.mc_version} · 加载器 {inst.loader} · {len(inst.mod_jars)} 个 mod{flags}")
    stats: list[HandlerStat] = []
    warnings: list[str] = []
    handlers = [h for h in all_handlers() if h.detect(inst)]
    with Workspace.for_instance(inst.root) as ws:
        ws.set_meta("mc_version", inst.mc_version)
        ws.set_meta("loader", inst.loader)
        ws.set_meta("has_i18nupdatemod", "1" if inst.has_i18nupdatemod else "0")
        run_id = ws.start_run("scan", {"mc_version": inst.mc_version})
        for i, handler in enumerate(handlers):
            reporter.progress(i, len(handlers))
            reporter.log(f"正在扫描：{handler.kind_label}")
            s = ws.sync_entries(handler.name, handler.extract(inst))
            stats.append(HandlerStat(handler.name, s.added, s.changed, s.unchanged, s.stale))
            warnings.extend(handler.warnings)
        reporter.progress(len(handlers), len(handlers))
        index = _load_cfpa(inst, settings, offline, reporter) if use_cfpa else None
        patches = PatchIndex(installed_patches(inst.root, warnings=warnings))
        if patches.patches:
            names = "、".join(p.name for p in patches.patches)
            reporter.log(f"检测到已安装的汉化补丁：{names}（共 {len(patches):,} 条译文，优先采用）")
        with Memory.default() as memory:
            match = run_matching(ws, index, memory, patches)
        # 纯符号 / 纯格式码 / 纯数字的条目无需翻译，扫描时直接标记，避免干扰进度与审校
        no_text = [e.id for e in ws.pending_entries() if not has_translatable_text(protect(e.original).text)]
        ws.save_translations([TranslationUpdate(i, None, None, "skipped") for i in no_text])
        ws.finish_run(run_id, {"entries": {s.name: s.added + s.changed + s.unchanged for s in stats},
                               "match": vars(match), "warnings": len(warnings)})
    reporter.log(
        f"采用 mod 自带中文 {match.builtin} 条，CFPA 人工译文 {match.cfpa} 条"
        f"（参数不一致、视为过时而跳过 {match.cfpa_rejected} 条），"
        f"记忆库 {match.memory} 条，记忆库中的人工译文 {match.human} 条"
        + (f"，汉化补丁 {match.respack} 条" if match.respack else "")
    )
    log_path = _write_log(inst.root, "scan", warnings) if warnings else None
    for w in warnings[:20]:
        reporter.log(w, "warn")
    if log_path:
        reporter.log(f"共 {len(warnings)} 条警告，完整列表见 {log_path}", "warn")
    return ScanResult(inst.mc_version, inst.loader, len(inst.mod_jars), inst.has_i18nupdatemod,
                      stats, match, warnings, log_path)


def _provider(settings: Settings, name: str | None) -> ProviderConfig:
    try:
        return settings.provider(name)
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc


def provider_problem(name: str | None = None) -> str | None:
    """翻译服务是否可用（配置能读取、API Key 已填写）；可用时返回 None，否则返回给用户看的原因。"""
    try:
        settings = load_settings()
        settings.provider(name).resolve_api_key()
    except ConfigError as exc:
        return str(exc)
    return None


def data_dir() -> Path:
    """全局数据目录：配置、翻译记忆库、CFPA 缓存、术语表、整合包列表。"""
    return home_dir()


def _select(ws: Workspace, only, limit, ids) -> list[Entry]:
    entries = ws.pending_entries()
    if ids is not None:
        wanted = set(ids)
        entries = [e for e in entries if e.id in wanted]
    return select_entries(entries, set(only) if only else None, limit)


def plan_translation(root: Path, provider: str | None = None, only=None, limit: int | None = None,
                     ids=None) -> TranslationPlan:
    settings = _settings()
    pcfg = _provider(settings, provider)
    with _workspace(root) as ws:
        if ids is None:  # 指定条目重新机翻时不再用记忆库填回旧译文
            with Memory.default() as memory:
                run_matching(ws, None, memory)
        entries = _select(ws, only, limit, ids)
    est = estimate(entries, EngineSettings.from_settings(settings), pcfg.price_input, pcfg.price_output)
    return TranslationPlan(entries, est, pcfg)


def quick_estimate(root: Path, provider: str | None = None) -> TranslationPlan | None:
    """不做记忆库匹配的快速预估（用于界面刷新）；未扫描时返回 None。"""
    if not summary(root).scanned:
        return None
    settings = _settings()
    pcfg = _provider(settings, provider)
    with _workspace(root) as ws:
        entries = ws.pending_entries()
    est = estimate(entries, EngineSettings.from_settings(settings), pcfg.price_input, pcfg.price_output)
    return TranslationPlan(entries, est, pcfg)


def describe_estimate(est: Estimate, pcfg: ProviderConfig) -> str:
    return (f"待翻译 {est.entries} 条（去重后 {est.units} 条），预计 {est.requests} 次请求，"
            f"输入约 {est.input_tokens:,} tokens、输出约 {est.output_tokens:,} tokens，"
            f"使用 {pcfg.name}/{pcfg.model} 预计费用约 {est.cost:.2f} 元（按配置单价估算，以官网为准）")


def translate(root: Path, provider: str | None = None, only=None, limit: int | None = None, ids=None,
              reporter: Reporter | None = None, announce: bool = True) -> TranslateReport:
    reporter = reporter or Reporter()
    settings = _settings()
    pcfg = _provider(settings, provider)
    engine_settings = EngineSettings.from_settings(settings)
    with _workspace(root) as ws:
        if ids is None:  # 指定条目重新机翻时不再用记忆库填回旧译文
            with Memory.default() as memory:
                run_matching(ws, None, memory)
        entries = _select(ws, only, limit, ids)
        est = estimate(entries, engine_settings, pcfg.price_input, pcfg.price_output)
        if announce:
            reporter.log(describe_estimate(est, pcfg))
        if est.entries == 0:
            return TranslateReport()
        try:
            client = client_from_config(pcfg)
        except ConfigError as exc:
            raise ServiceError(str(exc)) from exc
        glossary = build_glossary(ws.glossary_pairs(), load_user_glossary(home_dir() / "glossary.tsv"))
        run_id = ws.start_run("translate", {"provider": pcfg.name, "model": pcfg.model})
        done = 0
        reporter.progress(0, est.entries)

        def advance(n: int) -> None:
            nonlocal done
            done += n
            reporter.progress(done, est.entries)

        def on_error(count: int, reason: str, final: bool) -> None:
            if len(reason) > 160:
                reason = reason[:160] + "…"
            if final:
                reporter.log(f"{count} 条多次重试仍失败，已放弃（可在审校页手动处理或稍后重新翻译）：{reason}", "warn")
            else:
                reporter.log(f"{count} 条翻译失败，稍后自动重试：{reason}", "warn")

        report = run_translation(ws, client, glossary, engine_settings, on_progress=advance,
                                 entries=entries, should_stop=reporter.cancelled, on_error=on_error)
        ws.finish_run(run_id, vars(report))
        with Memory.default() as memory:
            remember_translations(ws, memory)
    if report.cancelled:
        reporter.log(f"已取消：翻译了 {report.translated} 条，其余条目下次继续", "warn")
    else:
        reporter.log(f"完成：翻译 {report.translated} 条，失败 {report.failed} 条，"
                     f"跳过（无需翻译）{report.skipped} 条，共 {report.requests} 次请求")
    usage = getattr(client, "usage", None)
    if usage is not None and usage.requests:
        reporter.log(describe_usage(usage, pcfg))
    if report.failed:
        reporter.log("有条目翻译失败，再次翻译会只重试失败的条目", "warn")
    return report


def describe_usage(usage, pcfg: ProviderConfig) -> str:
    """把接口返回的实际用量写成一句话；深度思考产生的推理 token 也按输出计费，单独提示。"""
    text = (f"实际用量：输入 {usage.input_tokens:,} tokens")
    if usage.cached_tokens:
        text += f"（其中命中缓存 {usage.cached_tokens:,}）"
    text += f"，输出 {usage.output_tokens:,} tokens"
    if usage.reasoning_tokens:
        text += f"（其中深度思考 {usage.reasoning_tokens:,}，可在设置中关闭深度思考以节省费用）"
    text += f"，按配置单价约 {usage.cost(pcfg.price_input, pcfg.price_output):.2f} 元"
    return text


def apply(root: Path, mode: str = "auto", set_lang: bool = False, dry_run: bool = False):
    with _workspace(root) as ws:
        try:
            chosen = apply_mod.resolve_mode(mode, ws.get_meta("has_i18nupdatemod") == "1")
            return apply_mod.apply(root, ws, ws.get_meta("mc_version"), mode=chosen,
                                   set_lang=set_lang, dry_run=dry_run)
        except (apply_mod.ApplyError, ValueError) as exc:
            raise ServiceError(str(exc)) from exc


def packs_above_ours(root: Path) -> list[str]:
    """已应用后，排在 mctrans 汉化包上面的资源包名（为空表示顺序正常）。"""
    with _workspace(root) as ws:
        mc_version = ws.get_meta("mc_version")
    return apply_mod.packs_above_ours(root, mc_version) if mc_version else []


def reapply_on_top(root: Path, mode: str = "auto", set_lang: bool = False, mc_version: str | None = None,
                   reporter: Reporter | None = None):
    """重新扫描（吸收已安装汉化补丁的译文）后重新应用，把汉化包放回最上面。"""
    reporter = reporter or Reporter()
    scan(root, mc_version, reporter=reporter)
    reporter.log("正在重新应用…")
    return apply(root, mode, set_lang)


def restore(root: Path):
    try:
        return apply_mod.restore(root)
    except apply_mod.ApplyError as exc:
        raise ServiceError(str(exc)) from exc


def is_applied(root: Path) -> bool:
    return apply_mod.is_applied(root)


def export_patch(root: Path, out_dir: Path, mode: str = "auto"):
    with _workspace(root) as ws:
        try:
            chosen = apply_mod.resolve_mode(mode, ws.get_meta("has_i18nupdatemod") == "1")
            return patch_mod.export_patch(root, ws, ws.get_meta("mc_version"), out_dir, chosen)
        except (apply_mod.ApplyError, ValueError) as exc:
            raise ServiceError(str(exc)) from exc


def review_export(root: Path, path: Path, include_all: bool = False, handlers=None) -> int:
    with _workspace(root) as ws:
        return export_review(ws, path, include_all=include_all, handlers=set(handlers) if handlers else None)


def review_import(root: Path, path: Path, force: bool = False) -> ImportReport:
    with _workspace(root) as ws:
        try:
            report = import_review(ws, path, force=force)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        with Memory.default() as memory:
            remember_translations(ws, memory)
    return report


def summary(root: Path) -> Summary:
    if not (Path(root) / ".mctrans" / "workspace.db").is_file():
        return Summary(scanned=False)
    with Workspace.for_instance(root) as ws:
        if ws.get_meta("mc_version") is None:
            return Summary(scanned=False)
        by_handler = ws.handler_status_counts()
        totals = Counter()
        for counts in by_handler.values():
            totals.update(counts)
        return Summary(
            scanned=True, mc_version=ws.get_meta("mc_version"), loader=ws.get_meta("loader"),
            has_i18nupdatemod=ws.get_meta("has_i18nupdatemod") == "1", last_scan=ws.last_run("scan"),
            by_handler=by_handler, origins=ws.origin_counts(), total=sum(totals.values()),
            finished=sum(totals[s] for s in FINISHED_STATUSES),
        )


def review_rows(root: Path) -> list[ReviewRow]:
    with _workspace(root) as ws:
        return ws.review_rows(include_all=True)


def token_problem(original: str, text: str) -> str | None:
    """格式码不一致时返回说明，否则返回 None。"""
    want, got = Counter(protect(original).tokens), Counter(protect(text).tokens)
    if want == got:
        return None
    missing = list((want - got).elements())
    extra = list((got - want).elements())
    parts = []
    if missing:
        parts.append("缺少 " + " ".join(missing))
    if extra:
        parts.append("多出 " + " ".join(extra))
    return "格式码与原文不一致：" + "，".join(parts)


def save_edits(root: Path, edits: dict[int, str]) -> dict[int, str]:
    rejected: dict[int, str] = {}
    updates: list[TranslationUpdate] = []
    with _workspace(root) as ws:
        for entry_id, text in edits.items():
            row = ws.get_review_row(entry_id)
            if row is None:
                rejected[entry_id] = "条目不存在"
                continue
            problem = token_problem(row.original, text)
            if problem:
                rejected[entry_id] = problem
                continue
            updates.append(TranslationUpdate(entry_id, text, "human", "confirmed"))
        ws.save_translations(updates)
        with Memory.default() as memory:
            remember_translations(ws, memory)
    return rejected


def confirm_entries(root: Path, ids) -> int:
    updates = []
    with _workspace(root) as ws:
        for entry_id in ids:
            row = ws.get_review_row(entry_id)
            if row is not None and row.text and row.status in ("done", "confirmed"):
                updates.append(TranslationUpdate(entry_id, row.text, "human", "confirmed"))
        n = ws.save_translations(updates)
        with Memory.default() as memory:
            remember_translations(ws, memory)
    return n


def requeue(root: Path, ids) -> int:
    with _workspace(root) as ws:
        return ws.requeue(list(ids))


def test_provider(provider: ProviderConfig) -> str:
    """发送一个极小的翻译请求，验证地址、Key 和模型名是否可用。"""
    try:
        client = client_from_config(provider)
        raw = client.complete(SYSTEM_PROMPT, build_user_prompt("minecraft", "测试", [], {"1": {"text": "Iron Ingot"}}))
        value = parse_response(raw).get("1")
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc
    except Exception as exc:  # 网络、鉴权、模型名错误等
        raise ServiceError(f"测试失败：{exc}") from exc
    if isinstance(value, dict):
        value = value.get("text")
    if not isinstance(value, str):
        raise ServiceError("测试失败：模型返回的格式不正确")
    return value
