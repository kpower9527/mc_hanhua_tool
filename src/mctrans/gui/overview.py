"""概览页：整合包信息、统计、操作按钮、进度与日志。"""
from __future__ import annotations

import html
import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import service
from ..review import ORIGIN_LABELS
from .review import HANDLER_LABELS
from .worker import TaskRunner

MODES = [("auto", "自动（检测 I18nUpdateMod）"), ("standalone", "独立资源包"), ("i18nupdate", "配合 I18nUpdateMod")]
STAT_COLUMNS = ["类别", "总数", "已完成", "待翻译", "失败", "完成度"]
LEVEL_COLORS = {"warn": "#b26a00", "error": "#c62828"}


class OverviewPage(QWidget):
    task_done = Signal(str)  # 任务名称：扫描 / 翻译 / 应用 / 还原 …

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
        actions.addWidget(self.scan_button, 0, 0)
        actions.addWidget(self.translate_button, 0, 1)
        actions.addWidget(self.apply_button, 0, 2)
        actions.addWidget(self.restore_button, 0, 3)
        actions.addWidget(self.more_button, 0, 4)
        options = QHBoxLayout()
        options.addWidget(self.set_lang)
        options.addSpacing(16)
        options.addWidget(QLabel("输出模式"))
        options.addWidget(self.mode_box)
        options.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress_label = QLabel()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.runner.cancel)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.cancel_button)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.info)
        layout.addWidget(stats_box)
        layout.addLayout(actions)
        layout.addLayout(options)
        layout.addLayout(progress_row)
        layout.addWidget(QLabel("日志"))
        layout.addWidget(self.log, 1)

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
        self.progress.setValue(0)
        self.progress.setMaximum(1)
        self.progress_label.setText("")
        self.refresh()

    def refresh(self) -> None:
        self.stats.setRowCount(0)
        self.origins.setText("")
        self.estimate.setText("")
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
        if not busy:
            self.progress_label.setText(self.progress_label.text() if self.progress.value() else "")

    # ---- 日志与进度 ----
    def append_log(self, text: str, level: str = "info") -> None:
        safe = html.escape(text).replace("\n", "<br>")
        color = LEVEL_COLORS.get(level)
        self.log.appendHtml(f'<span style="color:{color}">{safe}</span>' if color else safe)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        if self.runner.task_name == "翻译" and done and total:
            elapsed = time.monotonic() - self._started
            remain = elapsed / done * (total - done)
            self.progress_label.setText(f"{done}/{total}，预计剩余 {self._fmt(remain)}")
        else:
            self.progress_label.setText(f"{done}/{total}")

    @staticmethod
    def _fmt(seconds: float) -> str:
        seconds = int(seconds)
        return f"{seconds // 60} 分 {seconds % 60} 秒" if seconds >= 60 else f"{seconds} 秒"

    def _run(self, name: str, fn, *args, **kwargs) -> bool:
        if self.root is None or self.runner.busy:
            return False
        self._started = time.monotonic()
        self.progress.setMaximum(0)  # 未知进度时显示忙碌动画
        self.progress_label.setText(f"正在{name}…")
        self.append_log(f"—— 开始{name} ——")
        return self.runner.start(name, fn, *args, **kwargs)

    def _on_finished(self, result) -> None:
        name = self.runner.task_name
        if self.progress.maximum() == 0:
            self.progress.setMaximum(1)
            self.progress.setValue(1)
        self._describe(name, result)
        self.refresh()
        self.task_done.emit(name)

    def _on_failed(self, message: str) -> None:
        self.progress.setMaximum(1)
        self.progress.setValue(0)
        self.progress_label.setText("失败")
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
        self.progress_label.setText(f"{name}完成")

    # ---- 操作 ----
    def scan(self) -> bool:
        return self._run("扫描", service.scan, self.root, self.mc_version_override or None)

    def translate(self, only: list[str] | None = None, limit: int | None = None, ids: list[int] | None = None) -> bool:
        if self.root is None or self.runner.busy:
            return False
        if self.ask and ids is None:
            try:
                plan = service.plan_translation(self.root, only=only, limit=limit)
            except service.ServiceError as exc:
                QMessageBox.warning(self, "无法翻译", str(exc))
                return False
            if plan.estimate.entries == 0:
                QMessageBox.information(self, "翻译", "没有需要翻译的条目。")
                return False
            text = service.describe_estimate(plan.estimate, plan.provider)
            if QMessageBox.question(self, "开始翻译", text + "\n\n开始翻译吗？") != QMessageBox.Yes:
                return False
        return self._run("翻译", service.translate, self.root, None, only, limit, ids)

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
