# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：界面版 麦块岛MC一键汉化.exe 与命令行版 maikuai-cli.exe 共用一个目录。
# 用法：uv run python packaging/build.py
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - PyInstaller 注入
ICON = ROOT / "build" / "icon.ico"

# 用不到的 Qt 模块与标准库，排除以减小体积
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DExtras",
    "PySide6.Qt3DAnimation", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtTest", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSql", "PySide6.QtSvgWidgets",
    "PySide6.QtTextToSpeech", "PySide6.QtHttpServer", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtNetworkAuth",
]

common = dict(pathex=[str(ROOT / "src")], excludes=EXCLUDES, noarchive=False)

gui = Analysis([str(ROOT / "packaging" / "gui_entry.py")], **common)  # noqa: F821
cli = Analysis([str(ROOT / "packaging" / "cli_entry.py")], **common)  # noqa: F821

# 纯控件界面用不到的二进制：软件 OpenGL 备用库、被插件间接带入的 Quick / Qml / Pdf 等
DROP = ("opengl32sw.dll", "Qt6Quick", "Qt6Qml", "Qt6Pdf", "qpdf.dll", "Qt6VirtualKeyboard", "qtvirtualkeyboard")
for analysis in (gui, cli):
    analysis.binaries = [b for b in analysis.binaries if not any(d.lower() in b[0].lower() for d in DROP)]

gui_exe = EXE(  # noqa: F821
    PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="麦块岛MC一键汉化",  # noqa: F821
    console=False, icon=str(ICON), upx=False,
)
cli_exe = EXE(  # noqa: F821
    PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="maikuai-cli",  # noqa: F821
    console=True, icon=str(ICON), upx=False,
)
COLLECT(  # noqa: F821
    gui_exe, gui.binaries, gui.datas,
    cli_exe, cli.binaries, cli.datas,
    name="麦块岛MC一键汉化", upx=False,
)
