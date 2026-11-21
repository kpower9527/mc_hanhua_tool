"""构建 Windows 文件夹版：生成图标 → PyInstaller → 冒烟测试 → 打包 zip。

用法：uv run python packaging/build.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def make_icon(path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6.QtGui import QGuiApplication

    from mctrans.gui.app import make_icon_pixmap

    app = QGuiApplication.instance() or QGuiApplication([])
    path.parent.mkdir(parents=True, exist_ok=True)
    if not make_icon_pixmap(256).save(str(path), "ICO"):
        raise SystemExit("图标生成失败")
    del app


def run_pyinstaller() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), str(ROOT / "packaging" / "mctrans.spec")],
        check=True, cwd=ROOT,
    )


def smoke_test(app_dir: Path) -> None:
    env = dict(os.environ, MCTRANS_HOME=tempfile.mkdtemp(prefix="mctrans-smoke-"), PYTHONIOENCODING="utf-8")
    env.pop("QT_QPA_PLATFORM", None)
    cli = subprocess.run([str(app_dir / "mctrans-cli.exe"), "--help"], capture_output=True, env=env, timeout=120)
    out = cli.stdout.decode("utf-8", errors="replace")
    if cli.returncode != 0 or "scan" not in out:
        raise SystemExit(f"命令行版冒烟测试失败（返回码 {cli.returncode}）：\n{out}\n{cli.stderr.decode(errors='replace')}")
    print("命令行版冒烟测试通过")
    gui = subprocess.run([str(app_dir / "mctrans.exe"), "--smoke-test"], env=env, timeout=120)
    if gui.returncode != 0:
        raise SystemExit(f"界面版冒烟测试失败（返回码 {gui.returncode}）")
    print("界面版冒烟测试通过")


def make_zip(app_dir: Path, version: str) -> Path:
    target = DIST / f"mctrans-{version}-windows.zip"
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                z.write(path, Path("mctrans") / path.relative_to(app_dir))
        z.write(ROOT / "README.md", "mctrans/README.md")
    return target


def main() -> None:
    from mctrans import __version__

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST / "mctrans", ignore_errors=True)
    make_icon(BUILD / "icon.ico")
    run_pyinstaller()
    app_dir = DIST / "mctrans"
    smoke_test(app_dir)
    size = sum(p.stat().st_size for p in app_dir.rglob("*") if p.is_file()) / 1024 / 1024
    target = make_zip(app_dir, __version__)
    print(f"程序目录 {size:.0f} MB，压缩包 {target.stat().st_size / 1024 / 1024:.0f} MB：{target}")


if __name__ == "__main__":
    main()
