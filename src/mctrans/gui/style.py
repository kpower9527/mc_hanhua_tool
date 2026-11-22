"""界面样式：强调色与主要控件的外观。颜色用半透明灰做底，深色、浅色系统主题下都能看清。"""
from __future__ import annotations

ACCENT = "#2e7d32"
ACCENT_HOVER = "#388e3c"
ERROR = "#c62828"

STYLE = f"""
QProgressBar#taskProgress {{
    border: 1px solid rgba(128, 128, 128, 0.45);
    border-radius: 7px;
    background: rgba(128, 128, 128, 0.16);
    min-height: 26px;
    max-height: 26px;
    text-align: center;
    font-weight: bold;
    font-size: 13px;
}}
QProgressBar#taskProgress::chunk {{
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2e7d32, stop:1 #43a047);
}}
QProgressBar#taskProgress[state="error"]::chunk {{
    background: {ERROR};
}}
QFrame#taskPanel {{
    border: 1px solid rgba(128, 128, 128, 0.35);
    border-radius: 8px;
    background: rgba(128, 128, 128, 0.07);
}}
QFrame#taskPanel[state="busy"] {{
    border: 1px solid {ACCENT};
}}
QFrame#taskPanel[state="error"] {{
    border: 1px solid {ERROR};
}}
QLabel#taskTitle {{
    font-size: 14px;
    font-weight: bold;
}}
QPushButton#primary, QToolButton#primary {{
    background: {ACCENT};
    color: white;
    border: none;
    border-radius: 5px;
    font-weight: bold;
    padding: 4px 12px;
}}
QPushButton#primary:hover, QToolButton#primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#primary:disabled, QToolButton#primary:disabled {{
    background: rgba(46, 125, 50, 0.35);
    color: rgba(255, 255, 255, 0.55);
}}
QToolButton#primary::menu-button {{
    border: none;
    border-left: 1px solid rgba(255, 255, 255, 0.35);
    width: 26px;
}}
QFrame#welcome {{
    border: 1px solid rgba(128, 128, 128, 0.35);
    border-radius: 10px;
    background: rgba(128, 128, 128, 0.06);
}}
QLabel#welcomeTitle {{
    font-size: 20px;
    font-weight: bold;
}}
QLabel#stepTitle {{
    font-size: 14px;
    font-weight: bold;
}}
"""
