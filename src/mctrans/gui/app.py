"""图形界面入口。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from ..brand import APP_NAME


def make_icon_pixmap(size: int = 256) -> QPixmap:
    """绘制程序图标：绿色圆角方块上的「汉」字。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#3f8f3a"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.18, size * 0.18)
    painter.setBrush(QColor("#2f6e2b"))
    painter.drawRect(QRectF(0, size * 0.78, size, size * 0.22))
    painter.setPen(QColor("white"))
    font = QFont("Microsoft YaHei", int(size * 0.5))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(0, -size * 0.04, size, size * 0.9), Qt.AlignCenter, "汉")
    painter.end()
    return pixmap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke-test" in argv
    app = QApplication.instance() or QApplication([a for a in argv if a != "--smoke-test"])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(make_icon_pixmap()))
    from .main_window import MainWindow  # 延迟导入，便于先创建 QApplication

    window = MainWindow()
    window.show()
    if smoke:
        QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
