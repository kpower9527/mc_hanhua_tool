"""界面里的检查更新：后台线程查询 GitHub，有新版时弹窗提醒（只提醒，不自动下载）。"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QTextBrowser, QVBoxLayout, QWidget

from .. import __version__, update
from ..config import home_dir


class UpdateChecker(QObject):
    # (ReleaseInfo 或 None, 错误信息或 None, 是否手动检查)
    finished = Signal(object, object, bool)

    def __init__(self, parent: QWidget, check=update.check) -> None:
        super().__init__(parent)
        self._parent = parent
        self._check = check
        self._running = False
        self.finished.connect(self._on_finished)

    @property
    def running(self) -> bool:
        return self._running

    def start(self, manual: bool = False) -> bool:
        """开始检查；已在检查中时返回 False。"""
        if self._running:
            return False
        self._running = True

        def work() -> None:
            try:
                info, error = self._check(__version__), None
            except update.UpdateError as exc:
                info, error = None, str(exc)
            except Exception as exc:  # 后台线程里不能让异常丢失
                info, error = None, f"检查更新出错：{exc}"
            self.finished.emit(info, error, manual)

        threading.Thread(target=work, name="update-check", daemon=True).start()
        return True

    def _on_finished(self, info, error, manual: bool) -> None:
        self._running = False
        if error:
            if manual:
                QMessageBox.warning(self._parent, "检查更新", f"{error}\n\n可稍后重试，或直接访问：\n{update.RELEASES_URL}")
            return
        if not info.newer:
            if manual:
                QMessageBox.information(self._parent, "检查更新", f"当前已是最新版本（{__version__}）。")
            return
        if not manual and update.skipped_version(home_dir()) == info.version:
            return  # 用户选过「跳过此版本」，自动检查不再提醒
        UpdateDialog(self._parent, info).exec()


class UpdateDialog(QDialog):
    def __init__(self, parent: QWidget | None, info: update.ReleaseInfo) -> None:
        super().__init__(parent)
        self.info = info
        self.setWindowTitle("发现新版本")
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h3>新版本 {info.version} 已发布</h3><p>当前版本：{__version__}</p>"))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "（没有更新说明）")
        layout.addWidget(notes, 1)
        buttons = QDialogButtonBox()
        self.download_button = buttons.addButton("前往下载", QDialogButtonBox.AcceptRole)
        self.skip_button = buttons.addButton("跳过此版本", QDialogButtonBox.ActionRole)
        self.later_button = buttons.addButton("稍后", QDialogButtonBox.RejectRole)
        self.download_button.clicked.connect(self._download)
        self.skip_button.clicked.connect(self._skip)
        self.later_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _download(self) -> None:
        QDesktopServices.openUrl(QUrl(self.info.url))
        self.accept()

    def _skip(self) -> None:
        try:
            update.skip_version(home_dir(), self.info.version)
        except OSError:
            pass
        self.reject()
