"""主窗口：左侧整合包列表，右侧概览 / 审校页签。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .. import __version__, service
from ..instance import InstanceError, detect_mc_version
from ..instances import add_instance, find_instance, find_instance_dirs, load_instances, remove_instance
from .overview import OverviewPage
from .review import ReviewPage
from .settings import SettingsDialog
from .style import STYLE
from .worker import TaskRunner


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.base_title = f"mctrans 整合包汉化工具 {__version__}"
        self.setWindowTitle(self.base_title)
        self.setStyleSheet(STYLE)
        self.resize(1200, 780)
        self.runner = TaskRunner(self)
        self.ask = True  # 测试中设为 False

        self.instance_list = QListWidget()
        self.instance_list.currentItemChanged.connect(self._on_select)
        add_button = QPushButton("添加整合包…")
        add_button.setToolTip("选择整合包目录（包含 mods 文件夹）；选 .minecraft 时会列出其中版本隔离的整合包")
        add_button.clicked.connect(self._choose_instance)
        remove_button = QPushButton("从列表移除")
        remove_button.clicked.connect(self._remove_current)
        settings_button = QPushButton("设置")
        settings_button.clicked.connect(self.open_settings)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("整合包"))
        left_layout.addWidget(self.instance_list, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        left_layout.addLayout(buttons)
        left_layout.addWidget(settings_button)

        self.overview = OverviewPage(self.runner)
        self.review = ReviewPage()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.overview, "概览")
        self.tabs.addTab(self.review, "审校")
        self.tabs.currentChanged.connect(self._on_tab)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 940])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("就绪")

        self.overview.task_done.connect(self._on_task_done)
        self.overview.settings_requested.connect(self.open_settings)
        self.overview.add_requested.connect(self._choose_instance)
        self.runner.progress.connect(self._on_progress)
        self.review.apply_requested.connect(self._apply_from_review)
        self.review.retranslate_requested.connect(self._retranslate)
        self.review.message.connect(lambda text, level: self.statusBar().showMessage(text, 8000))
        self.runner.busy_changed.connect(self._on_busy)
        self._review_dirty = True
        self.reload_instances()

    # ---- 整合包列表 ----
    def reload_instances(self, select: str | None = None) -> None:
        self.instance_list.blockSignals(True)
        self.instance_list.clear()
        for record in load_instances():
            item = QListWidgetItem(self._item_text(record.path, record.name))
            item.setData(Qt.UserRole, record.path)
            item.setToolTip(record.path)
            self.instance_list.addItem(item)
        self.instance_list.blockSignals(False)
        target = select or (load_instances()[0].path if load_instances() else None)
        for i in range(self.instance_list.count()):
            if self.instance_list.item(i).data(Qt.UserRole) == target:
                self.instance_list.setCurrentRow(i)
                break
        else:
            self._on_select(None, None)

    @staticmethod
    def _item_text(path: str, name: str) -> str:
        if not Path(path).is_dir():
            return f"{name}\n（目录不存在）"
        s = service.summary(Path(path))
        if not s.scanned:
            return f"{name}\n未扫描"
        percent = s.finished * 100 // max(s.total, 1)
        return f"{name}\nMC {s.mc_version} · 已完成 {percent}%"

    def _refresh_current_item(self) -> None:
        item = self.instance_list.currentItem()
        if item is not None:
            path = item.data(Qt.UserRole)
            item.setText(self._item_text(path, Path(path).name))

    def _choose_instance(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择整合包目录（包含 mods 文件夹的那一层，或者 .minecraft）")
        if path:
            self.add_instance_path(path)

    def add_instance_path(self, path: str | Path, mc_version: str = "") -> bool:
        root = Path(path).resolve()
        if not (root / "mods").is_dir():
            found = find_instance_dirs(root)
            if not found:
                self._warn("不是整合包目录", f"{root} 下没有 mods 文件夹，也没有找到版本隔离的整合包。\n"
                                         "请选择包含 mods、config 文件夹的那一层目录。")
                return False
            if len(found) == 1 or not self.ask:
                root = found[0]
            else:
                names = [d.name for d in found]
                name, ok = QInputDialog.getItem(self, "选择整合包", f"在 {root} 中找到 {len(found)} 个整合包，"
                                                "请选择要添加的一个：", names, 0, False)
                if not ok:
                    return False
                root = found[names.index(name)]
        if not mc_version and detect_mc_version(root) is None:
            if not self.ask:
                return False
            mc_version, ok = QInputDialog.getText(self, "无法识别版本", "无法自动识别 Minecraft 版本，请手动填写（如 1.20.1）：")
            if not ok or not mc_version.strip():
                return False
        add_instance(root, mc_version.strip())
        self.reload_instances(select=str(root))
        if not service.summary(root).scanned:
            self.overview.scan()
        return True

    def _remove_current(self) -> None:
        item = self.instance_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if self.ask and QMessageBox.question(
                self, "移除", f"从列表中移除 {Path(path).name}？\n（只是不再显示，不会删除任何文件）") != QMessageBox.Yes:
            return
        remove_instance(path)
        self.reload_instances()

    def current_root(self) -> Path | None:
        item = self.instance_list.currentItem()
        return Path(item.data(Qt.UserRole)) if item is not None else None

    def _on_select(self, current, _previous) -> None:
        if self.runner.busy:
            return
        root = Path(current.data(Qt.UserRole)) if current is not None else None
        if root is not None and not root.is_dir():
            self._warn("目录不存在", f"{root}\n这个整合包目录已经不存在，可以从列表中移除。")
            root = None
        record = find_instance(root) if root else None
        self.overview.set_instance(root, record.mc_version if record else "")
        self._review_dirty = True
        if self.tabs.currentWidget() is self.review:
            self._load_review()

    def _on_tab(self, _index: int) -> None:
        if self.tabs.currentWidget() is self.review and self._review_dirty:
            self._load_review()

    def _load_review(self) -> None:
        self.review.set_instance(self.overview.root)
        self._review_dirty = False

    # ---- 任务联动 ----
    def _on_task_done(self, name: str) -> None:
        self._refresh_current_item()
        if name == "应用":
            self.review.mark_applied()
        if name in ("扫描", "翻译", "导入"):
            self._review_dirty = True
            if self.tabs.currentWidget() is self.review:
                self._load_review()
        self.statusBar().showMessage(f"{name}完成", 8000)

    def _on_progress(self, done: int, total: int) -> None:
        # 窗口标题显示百分比：最小化或切到别的窗口时，在任务栏上也能看到进度
        if total:
            self.setWindowTitle(f"[{done * 100 // total}%] {self.runner.task_name} - {self.base_title}")

    def _on_busy(self, busy: bool) -> None:
        if not busy:
            self.setWindowTitle(self.base_title)
        self.instance_list.setEnabled(not busy)
        self.review.set_busy(busy)
        self.statusBar().showMessage(f"正在{self.runner.task_name}…" if busy else "就绪")

    def _apply_from_review(self) -> None:
        self.tabs.setCurrentWidget(self.overview)
        self.overview.apply()

    def _retranslate(self, ids: list[int]) -> None:
        self.tabs.setCurrentWidget(self.overview)
        self.overview.retranslate(ids)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec():
            self.statusBar().showMessage("设置已保存", 5000)
        self.overview.refresh()

    def _warn(self, title: str, text: str) -> None:
        if self.ask:
            QMessageBox.warning(self, title, text)
        else:
            self.statusBar().showMessage(text)

    def closeEvent(self, event) -> None:
        if self.runner.busy:
            if self.ask and QMessageBox.question(
                    self, "正在运行", f"正在{self.runner.task_name}，确定要退出吗？已完成的部分会保留。") != QMessageBox.Yes:
                event.ignore()
                return
            self.runner.cancel()
            self.runner.wait()
        event.accept()
