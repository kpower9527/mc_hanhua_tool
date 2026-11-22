"""概览页：整合包信息、统计、操作按钮、进度与日志。"""
from __future__ import annotations

import html
import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import service
from ..review import ORIGIN_LABELS
from .review import HANDLER_LABELS
from .worker import TaskRunner

MODES = [("auto", "自动（检测 I18nUpdateMod）"), ("standalone", "独立资源包"), ("i18nupdate", "配合 I18nUpdateMod")]
STAT_COLUMNS = ["类别", "总数", "已完成", "待翻译", "失败", "完成度"]
LEVEL_COLORS = {"warn": "#b26a00", "error": "#c62828"}


class WelcomePanel(QFrame):
    """首次使用引导：没有选中整合包时显示，列出三步并标出已完成的步骤。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("welcome")
        title = QLabel("欢迎使用 mctrans 整合包汉化工具")
        title.setObjectName("welcomeTitle")
        intro = QLabel("先用 mod 自带中文、CFPA 社区汉化和翻译记忆免费补全，剩下的再交给大模型翻译。"
                       "所有修改都会自动备份，可以一键还原。")
        intro.setWordWrap(True)
        self.step1 = QLabel()
        self.step1.setObjectName("stepTitle")
        step1_text = QLabel('翻译需要大模型服务的 API Key。推荐通义千问 qwen3.7-flash（便宜、速度快），'
                            '大型整合包通常只需 1 元左右。<br>获取 API Key：'
                            '<a href="https://bailian.console.aliyun.com/?tab=model#/api-key">阿里云百炼</a> · '
                            '<a href="https://platform.deepseek.com/api_keys">DeepSeek</a>')
        step1_text.setWordWrap(True)
        step1_text.setOpenExternalLinks(True)
        self.settings_button = QPushButton("打开设置，填写 API Key")
        self.step2 = QLabel()
        self.step2.setObjectName("stepTitle")
        step2_text = QLabel("选择整合包所在的目录（里面有 mods、config 文件夹）。启动器开了“版本隔离”时，"
                            "整合包在 .minecraft\\versions\\<整合包名> 下；直接选 .minecraft 也可以，会让你挑选。")
        step2_text.setWordWrap(True)
        step2_text.setTextFormat(Qt.PlainText)
        self.add_button = QPushButton("添加整合包…")
        self.add_button.setObjectName("primary")
        step3 = QLabel("3. 扫描 → 翻译 → 应用到游戏")
        step3.setObjectName("stepTitle")
        step3_text = QLabel("添加后会自动扫描。点「翻译」前会先显示预计费用，确认后才开始；"
                            "翻译完成点「应用到游戏」，进游戏即可看到中文。不满意可以在「审校」页修改，或点「还原」撤销。")
        step3_text.setWordWrap(True)
        for button in (self.settings_button, self.add_button):
            button.setMinimumHeight(34)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.setMinimumWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addSpacing(10)
        for widget in (self.step1, step1_text, self.settings_button):
            layout.addWidget(widget)
        layout.addSpacing(10)
        for widget in (self.step2, step2_text, self.add_button):
            layout.addWidget(widget)
        layout.addSpacing(10)
        layout.addWidget(step3)
        layout.addWidget(step3_text)
        layout.addStretch(1)

    def update_status(self, provider_problem: str | None, has_instance: bool) -> None:
        self.step1.setText(("✓ " if provider_problem is None else "") + "1. 设置翻译服务")
        self.settings_button.setText("已设置，可在此修改" if provider_problem is None else "打开设置，填写 API Key")
        self.settings_button.setObjectName("" if provider_problem is None else "primary")
        self.add_button.setObjectName("primary" if provider_problem is None else "")
        for button in (self.settings_button, self.add_button):
            button.style().unpolish(button)
            button.style().polish(button)
        self.step2.setText(("✓ " if has_instance else "") + "2. 添加整合包")


class OverviewPage(QWidget):
    task_done = Signal(str)  # 任务名称：扫描 / 翻译 / 应用 / 还原 …
    settings_requested = Signal()
    add_requested = Signal()

    def __init__(self, runner: TaskRunner, parent=None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.root: Path | None = None
        self.mc_version_override = ""
        self.ask = True          # 测试中设为 False，跳过确认对话框
        self._started = 0.0

        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.stats = QTableWidget(0, len(STAT_COLUMNS))
        self.stats.setHorizontalHeaderLabels(STAT_COLUMNS)
        self.stats.verticalHeader().setVisible(False)
        self.stats.setEditTriggers(QTableWidget.NoEditTriggers)
        self.stats.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats.setMaximumHeight(220)
        self.origins = QLabel()
        self.origins.setWordWrap(True)
        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        stats_box = QGroupBox("翻译进度")
        stats_layout = QVBoxLayout(stats_box)
        stats_layout.addWidget(self.stats)
        stats_layout.addWidget(self.origins)
        stats_layout.addWidget(self.estimate)

        self.scan_button = QPushButton("扫描")
        self.scan_button.setToolTip("提取待翻译文本，并匹配 mod 自带中文、CFPA 社区译文和翻译记忆")
        self.scan_button.clicked.connect(self.scan)
        self.translate_button = QToolButton()
        self.translate_button.setText("翻译")
        self.translate_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.translate_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.translate_button.clicked.connect(lambda: self.translate())
        self.translate_menu = QMenu(self)
        self.translate_button.setMenu(self.translate_menu)
        self.apply_button = QPushButton("应用到游戏")
        self.apply_button.clicked.connect(self.apply)
        self.restore_button = QPushButton("还原")
        self.restore_button.clicked.connect(self.restore)
        self.more_button = QToolButton()
        self.more_button.setText("更多")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        more = QMenu(self)
        more.addAction("导出汉化补丁…", self.export_patch)
        more.addSeparator()
        more.addAction("导出审校 Excel…", self.export_excel)
        more.addAction("导入审校 Excel…", self.import_excel)
        more.addSeparator()
        more.addAction("打开实例目录", self.open_folder)
        self.more_button.setMenu(more)

        self.set_lang = QCheckBox("同时把游戏语言设为简体中文")
        self.set_lang.setChecked(True)
        self.mode_box = QComboBox()
        for value, label in MODES:
            self.mode_box.addItem(label, value)

        actions = QGridLayout()
        buttons = (self.scan_button, self.translate_button, self.apply_button, self.restore_button, self.more_button)
        for col, button in enumerate(buttons):
            # 所有操作按钮同样大小：工具按钮默认只按文字宽度收缩，这里统一拉伸
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setMinimumHeight(34)
            actions.addWidget(button, 0, col)
            actions.setColumnStretch(col, 1)
        self.translate_button.setToolTip("点击翻译全部待翻译条目；点右侧箭头可以试翻 50 条或只翻某一类")
        self.translate_button.setObjectName("primary")
        self.apply_button.setObjectName("primary")
        options = QHBoxLayout()
        options.addWidget(self.set_lang)
        options.addSpacing(16)
        options.addWidget(QLabel("输出模式"))
        options.addWidget(self.mode_box)
        options.addStretch(1)

        # 当前任务面板：任务名 + 粗进度条 + 详情，运行中边框高亮
        self.task_panel = QFrame()
        self.task_panel.setObjectName("taskPanel")
        self.task_title = QLabel("空闲")
        self.task_title.setObjectName("taskTitle")
        self.progress = QProgressBar()
        self.progress.setObjectName("taskProgress")
        self.progress.setTextVisible(True)
        self.progress_label = QLabel()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setMinimumWidth(80)
        self.cancel_button.clicked.connect(self.runner.cancel)
        panel = QGridLayout(self.task_panel)
        panel.setContentsMargins(12, 10, 12, 10)
        panel.addWidget(self.task_title, 0, 0)
        panel.addWidget(self.progress_label, 0, 1, 1, 2, Qt.AlignRight)
        panel.addWidget(self.progress, 1, 0, 1, 2)
        panel.addWidget(self.cancel_button, 1, 2)
        panel.setColumnStretch(0, 1)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._update_detail)
        self._done = self._total = 0
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.info)
        layout.addWidget(stats_box)
        layout.addLayout(actions)
        layout.addLayout(options)
        layout.addWidget(self.task_panel)
        layout.addWidget(QLabel("日志"))
        layout.addWidget(self.log, 1)
        self.welcome = WelcomePanel()
        self.welcome.settings_button.clicked.connect(self.settings_requested)
        self.welcome.add_button.clicked.connect(self.add_requested)
        outer = QVBoxLayout(self)
        outer.addWidget(self.welcome)
        outer.addWidget(self.content, 1)

        runner.log.connect(self.append_log)
        runner.progress.connect(self._on_progress)
        runner.busy_changed.connect(self._update_buttons)
        runner.finished.connect(self._on_finished)
        runner.failed.connect(self._on_failed)
        self.set_instance(None)

    # ---- 状态 ----
    def set_instance(self, root: Path | None, mc_version: str = "") -> None:
        self.root = root
        self.mc_version_override = mc_version
        self.log.clear()
        self._set_task("空闲", "idle")
        self.progress.setMaximum(1)
        self.progress.setValue(0)
        self.progress_label.setText("")
        self.refresh()

    def refresh(self) -> None:
        self.stats.setRowCount(0)
        self.origins.setText("")
        self.estimate.setText("")
        self.welcome.update_status(service.provider_problem(), self.root is not None)
        self.welcome.setVisible(self.root is None)
        self.content.setVisible(self.root is not None)
        if self.root is None:
            self.title.setText("请在左侧添加或选择一个整合包")
            self.info.setText("")
            self._update_buttons(self.runner.busy)
            return
        self.title.setText(self.root.name)
        summary = service.summary(self.root)
        self.summary = summary
        if not summary.scanned:
            self.info.setText(f"{self.root}\n还没有扫描过，点击「扫描」开始。")
            self._update_buttons(self.runner.busy)
            return
        i18n = " · 检测到 I18nUpdateMod" if summary.has_i18nupdatemod else ""
        applied = " · 已应用到游戏" if service.is_applied(self.root) else ""
        last = ""
        if summary.last_scan:
            try:  # 数据库中是 UTC 时间，显示为本地时间
                last = datetime.fromisoformat(summary.last_scan).astimezone().strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last = summary.last_scan
        self.info.setText(f"{self.root}\nMinecraft {summary.mc_version} · {summary.loader}{i18n}{applied}"
                          f" · 上次扫描 {last}")
        for handler, counts in summary.by_handler.items():
            total = sum(counts.values())
            finished = sum(counts.get(s, 0) for s in service.FINISHED_STATUSES)
            values = [HANDLER_LABELS.get(handler, handler), total, finished, counts.get("pending", 0),
                      counts.get("failed", 0), f"{finished * 100 // max(total, 1)}%"]
            r = self.stats.rowCount()
            self.stats.insertRow(r)
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if c:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.stats.setItem(r, c, item)
        parts = [f"{ORIGIN_LABELS.get(k, k)} {v}" for k, v in sorted(summary.origins.items(), key=lambda kv: -kv[1])]
        self.origins.setText("译文来源：" + ("，".join(parts) if parts else "暂无"))
        try:
            plan = service.quick_estimate(self.root)
        except service.ServiceError as exc:
            self.estimate.setText(f"无法预估费用：{exc}")
        else:
            if plan is not None:
                self.estimate.setText(service.describe_estimate(plan.estimate, plan.provider))
        self._rebuild_translate_menu()
        self._update_buttons(self.runner.busy)

    def _rebuild_translate_menu(self) -> None:
        menu = self.translate_menu
        menu.clear()
        menu.addAction("翻译全部待翻译条目", lambda: self.translate())
        menu.addAction("试翻 50 条（先看效果）", lambda: self.translate(limit=50))
        menu.addSeparator()
        for handler, counts in getattr(self, "summary", service.Summary(False)).by_handler.items():
            pending = counts.get("pending", 0) + counts.get("failed", 0)
            if pending:
                label = f"只翻译{HANDLER_LABELS.get(handler, handler)}（{pending} 条）"
                menu.addAction(label, lambda h=handler: self.translate(only=[h]))

    def _update_buttons(self, busy: bool) -> None:
        has = self.root is not None
        scanned = has and getattr(self, "summary", None) is not None and self.summary.scanned
        self.scan_button.setEnabled(has and not busy)
        for widget in (self.translate_button, self.apply_button, self.more_button):
            widget.setEnabled(bool(scanned) and not busy)
        self.restore_button.setEnabled(has and not busy and service.is_applied(self.root))
        self.cancel_button.setEnabled(busy and self.runner.task_name == "翻译")

    # ---- 日志与进度 ----
    def append_log(self, text: str, level: str = "info") -> None:
        safe = html.escape(text).replace("\n", "<br>")
        color = LEVEL_COLORS.get(level)
        self.log.appendHtml(f'<span style="color:{color}">{safe}</span>' if color else safe)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.progress.setFormat("%p%")
        self._done, self._total = done, total
        self._update_detail()

    def _update_detail(self) -> None:
        """详情：已完成/总数 · 已用时间 · 预计剩余（每秒刷新，长任务也能看出在动）。"""
        if not self.runner.busy:
            return
        elapsed = time.monotonic() - self._started
        parts = []
        if self._total:
            parts.append(f"{self._done:,} / {self._total:,}")
        parts.append(f"已用 {self._fmt(elapsed)}")
        if self.runner.task_name == "翻译" and self._done and self._total > self._done:
            parts.append(f"预计剩余 {self._fmt(elapsed / self._done * (self._total - self._done))}")
        self.progress_label.setText(" · ".join(parts))

    def _set_task(self, title: str, state: str) -> None:
        self.task_title.setText(title)
        for widget in (self.task_panel, self.progress):
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    @staticmethod
    def _fmt(seconds: float) -> str:
        seconds = int(seconds)
        return f"{seconds // 60} 分 {seconds % 60} 秒" if seconds >= 60 else f"{seconds} 秒"

    def _run(self, name: str, fn, *args, **kwargs) -> bool:
        if self.root is None or self.runner.busy:
            return False
        self._started = time.monotonic()
        self._done = self._total = 0
        self.progress.setMaximum(0)  # 未知进度时显示忙碌动画
        self.progress.setValue(0)
        self._set_task(f"正在{name}…", "busy")
        self.progress_label.setText("")
        self.append_log(f"—— 开始{name} ——")
        started = self.runner.start(name, fn, *args, **kwargs)
        if started:
            self._tick.start()
        return started

    def _on_finished(self, result) -> None:
        name = self.runner.task_name
        self._tick.stop()
        if self.progress.maximum() == 0:
            self.progress.setMaximum(1)
            self.progress.setValue(1)
        if name == "预估":
            self._set_task("空闲", "idle")
            self.progress.setValue(0)
            self.progress_label.setText("")
            self._confirm_translate(result)
            return
        elapsed = self._fmt(time.monotonic() - self._started)
        cancelled = getattr(result, "cancelled", False)
        self._set_task(f"{name}已取消" if cancelled else f"✓ {name}完成", "idle")
        self.progress_label.setText(f"用时 {elapsed}")
        self._describe(name, result)
        self.refresh()
        self.task_done.emit(name)

    def _on_failed(self, message: str) -> None:
        self._tick.stop()
        self.progress.setMaximum(1)
        self.progress.setValue(1)
        self.progress.setFormat("失败")
        self._set_task(f"✗ {self.runner.task_name}失败", "error")
        self.progress_label.setText("详情见日志")
        self.append_log(message, "error")
        if self.ask:
            QMessageBox.warning(self, "操作失败", message)
        self.refresh()

    def _describe(self, name: str, result) -> None:
        if name == "应用":
            mode = "配合 I18nUpdateMod" if result.mode == "i18nupdate" else "独立资源包"
            self.append_log(f"已应用到游戏（{mode}），写入译文 {result.entries} 条")
            for pack, n in result.packs.items():
                self.append_log(f"资源包：resourcepacks/{pack}（{n} 个文件）")
            for rel in result.instance_files:
                self.append_log(f"修改文件：{rel}")
            self.append_log("修改前已自动备份，可以随时点击「还原」。")
        elif name == "还原":
            self.append_log(f"已还原 {len(result.restored)} 个文件，删除 {len(result.removed)} 个生成文件")
            for rel in result.skipped:
                self.append_log(f"跳过（应用后被改动过）：{rel}", "warn")
        elif name == "导出补丁":
            self.append_log(f"已导出补丁（译文 {result.entries} 条，文件 {len(result.files)} 个）")
            if result.includes_cfpa:
                self.append_log("补丁包含 CFPA 社区译文（CC BY-NC-SA 4.0），分发时请保留说明文件，且不得商用", "warn")

    # ---- 操作 ----
    def scan(self) -> bool:
        return self._run("扫描", service.scan, self.root, self.mc_version_override or None)

    def translate(self, only: list[str] | None = None, limit: int | None = None, ids: list[int] | None = None) -> bool:
        if self.root is None or self.runner.busy:
            return False
        problem = service.provider_problem()
        if problem is not None:
            self.append_log(problem, "error")
            if self.ask and QMessageBox.question(
                    self, "还不能翻译", f"{problem}\n\n现在打开设置填写 API Key 吗？") == QMessageBox.Yes:
                self.settings_requested.emit()
            return False
        if self.ask and ids is None:
            # 先在后台预估费用（大整合包需要匹配记忆库），完成后再弹确认框，界面不会卡住
            self._pending_translate = (only, limit)
            return self._run("预估", service.plan_translation, self.root, None, only, limit)
        return self._run("翻译", service.translate, self.root, None, only, limit, ids)

    def _confirm_translate(self, plan) -> None:
        only, limit = self._pending_translate
        if plan.estimate.entries == 0:
            self.append_log("没有需要翻译的条目。")
            QMessageBox.information(self, "翻译", "没有需要翻译的条目。")
            return
        text = service.describe_estimate(plan.estimate, plan.provider)
        self.append_log(text)
        if QMessageBox.question(self, "开始翻译", text + "\n\n开始翻译吗？") == QMessageBox.Yes:
            self._run("翻译", service.translate, self.root, None, only, limit, None)

    def retranslate(self, ids: list[int]) -> bool:
        if self.root is None or self.runner.busy:
            return False
        service.requeue(self.root, ids)
        return self.translate(ids=ids)

    def apply(self) -> bool:
        return self._run("应用", service.apply, self.root, self.mode_box.currentData(), self.set_lang.isChecked())

    def restore(self) -> bool:
        if self.ask and QMessageBox.question(self, "还原", "撤销对这个整合包的所有汉化修改吗？") != QMessageBox.Yes:
            return False
        return self._run("还原", service.restore, self.root)

    def export_patch(self, out_dir: str | None = None) -> bool:
        if out_dir is None:
            out_dir = QFileDialog.getExistingDirectory(self, "选择一个空文件夹保存汉化补丁")
            if not out_dir:
                return False
        return self._run("导出补丁", service.export_patch, self.root, Path(out_dir), self.mode_box.currentData())

    def export_excel(self, path: str | None = None) -> None:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "导出审校表格", f"{self.root.name}-审校.xlsx", "Excel (*.xlsx)")
            if not path:
                return
        try:
            n = service.review_export(self.root, Path(path))
        except service.ServiceError as exc:
            self.append_log(str(exc), "error")
            return
        self.append_log(f"已导出 {n} 条到 {path}")

    def import_excel(self, path: str | None = None) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "导入审校表格", "", "Excel (*.xlsx)")
            if not path:
                return
        try:
            report = service.review_import(self.root, Path(path))
        except service.ServiceError as exc:
            self.append_log(str(exc), "error")
            return
        self.append_log(f"导入完成：修改 {report.updated} 条，确认 {report.confirmed} 条，未改动 {report.unchanged} 条，"
                        f"找不到的 ID {report.unknown} 条，拒绝 {len(report.invalid)} 条")
        for line in report.invalid[:20]:
            self.append_log(line, "warn")
        self.refresh()
        self.task_done.emit("导入")

    def open_folder(self) -> None:
        if self.root is not None and hasattr(os, "startfile"):
            os.startfile(self.root)  # noqa: S606 - 打开用户自己的目录
