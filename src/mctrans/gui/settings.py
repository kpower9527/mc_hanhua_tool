"""设置窗口：翻译服务、运行参数、CFPA、术语表、配置文本规则。"""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import service
from ..config import ConfigError, ProviderConfig, load_settings
from ..handlers.config_text import RULES_FILE, parse_rules
from ..config import home_dir
from ..settings_io import save_settings, save_user_glossary
from ..translate.glossary import load_user_glossary
from .worker import TaskRunner

PROVIDER_LABELS = {"deepseek": "DeepSeek", "qwen": "通义千问（阿里云百炼）"}
RULES_TEMPLATE = """# 配置文本规则示例（删除行首的 # 启用）：
# [[rule]]
# glob = "config/loadingtips/*.txt"      # 相对实例目录的文件匹配
# pattern = '^tip=(?P<text>.+)$'          # 多行正则，命名组 text 是要翻译的部分
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(640, 520)
        try:
            self.settings = load_settings()
        except ConfigError as exc:
            QMessageBox.warning(self, "配置文件有误", f"{exc}\n\n将使用默认设置。")
            from ..config import Settings, DEFAULT_PROVIDERS
            self.settings = Settings(providers={n: ProviderConfig(name=n, **v) for n, v in DEFAULT_PROVIDERS.items()})
        self.providers = copy.deepcopy(self.settings.providers)
        self._editing: str | None = None
        self.runner = TaskRunner(self)
        self.runner.finished.connect(lambda text: self._set_test_result(f"✓ 连接成功，测试译文：{text}", True))
        self.runner.failed.connect(lambda msg: self._set_test_result(f"✗ {msg}", False))

        tabs = QTabWidget()
        tabs.addTab(self._provider_tab(), "翻译服务")
        tabs.addTab(self._runtime_tab(), "运行参数")
        tabs.addTab(self._cfpa_tab(), "CFPA 汉化包")
        tabs.addTab(self._glossary_tab(), "术语表")
        tabs.addTab(self._rules_tab(), "配置文本规则")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ---- 翻译服务 ----
    def _provider_tab(self) -> QWidget:
        page = QWidget()
        self.default_box = QComboBox()
        self.edit_box = QComboBox()
        self._fill_provider_boxes(self.settings.default_provider)
        self.edit_box.currentIndexChanged.connect(self._switch_provider)
        add_button = QPushButton("新增自定义服务…")
        add_button.clicked.connect(self._add_provider)
        self.base_url = QLineEdit()
        self.model_edit = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("留空则读取环境变量")
        self.key_env_label = QLabel()
        self.price_in = QDoubleSpinBox()
        self.price_out = QDoubleSpinBox()
        for box in (self.price_in, self.price_out):
            box.setRange(0, 1000)
            box.setDecimals(3)
            box.setSuffix(" 元/百万 tokens")
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self.test_connection)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)

        top = QFormLayout()
        top.addRow("默认翻译服务", self.default_box)
        edit_row = QHBoxLayout()
        edit_row.addWidget(self.edit_box, 1)
        edit_row.addWidget(add_button)
        top.addRow("编辑服务", edit_row)
        form = QFormLayout()
        form.addRow("接口地址", self.base_url)
        form.addRow("模型名称", self.model_edit)
        form.addRow("API Key", self.api_key)
        form.addRow("", self.key_env_label)
        form.addRow("输入单价", self.price_in)
        form.addRow("输出单价", self.price_out)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        layout = QVBoxLayout(page)
        layout.addLayout(top)
        layout.addSpacing(8)
        layout.addLayout(form)
        layout.addLayout(test_row)
        layout.addWidget(QLabel("单价仅用于费用预估，以服务商官网为准。"))
        layout.addStretch(1)
        self._switch_provider()
        return page

    def _fill_provider_boxes(self, default: str) -> None:
        for box in (self.default_box, self.edit_box):
            box.blockSignals(True)
            box.clear()
            for name in self.providers:
                box.addItem(PROVIDER_LABELS.get(name, name), name)
            box.blockSignals(False)
        self.default_box.setCurrentIndex(max(0, self.default_box.findData(default)))
        self.edit_box.setCurrentIndex(max(0, self.edit_box.findData(default)))

    def _store_form(self) -> None:
        if self._editing and self._editing in self.providers:
            p = self.providers[self._editing]
            p.base_url = self.base_url.text().strip()
            p.model = self.model_edit.text().strip()
            p.api_key = self.api_key.text().strip()
            p.price_input = self.price_in.value()
            p.price_output = self.price_out.value()

    def _switch_provider(self) -> None:
        self._store_form()
        name = self.edit_box.currentData()
        self._editing = name
        if name is None:
            return
        p = self.providers[name]
        self.base_url.setText(p.base_url)
        self.model_edit.setText(p.model)
        self.api_key.setText(p.api_key)
        self.key_env_label.setText(f"未填写时读取环境变量 {p.api_key_env}" if p.api_key_env else "")
        self.price_in.setValue(p.price_input)
        self.price_out.setValue(p.price_output)
        self.test_result.setText("")

    def _add_provider(self) -> None:
        name, ok = QInputDialog.getText(self, "新增自定义服务", "服务名称（英文、数字或下划线）：")
        name = name.strip()
        if ok and name:
            self.add_provider(name)

    def add_provider(self, name: str) -> None:
        if name in self.providers:
            return
        self._store_form()
        self.providers[name] = ProviderConfig(name=name, base_url="http://localhost:11434/v1", model="")
        default = self.default_box.currentData()
        self._fill_provider_boxes(default)
        self.edit_box.setCurrentIndex(self.edit_box.findData(name))
        self._editing = None
        self._switch_provider()

    def test_connection(self) -> None:
        self._store_form()
        provider = copy.deepcopy(self.providers[self.edit_box.currentData()])
        self.test_result.setText("正在测试…")
        self.test_result.setStyleSheet("")
        self.runner.start("测试连接", service.test_provider, provider)

    def _set_test_result(self, text: str, ok: bool) -> None:
        self.test_result.setText(text)
        self.test_result.setStyleSheet("color: #2e7d32;" if ok else "color: #c62828;")

    # ---- 运行参数 / CFPA ----
    def _runtime_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 32)
        self.concurrency.setValue(self.settings.concurrency)
        self.rpm = QSpinBox()
        self.rpm.setRange(0, 100000)
        self.rpm.setValue(self.settings.requests_per_minute)
        self.rpm.setSpecialValueText("不限")
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 200)
        self.batch_size.setValue(self.settings.batch_size)
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        self.retries.setValue(self.settings.max_retries)
        form.addRow("并发请求数", self.concurrency)
        form.addRow("每分钟请求上限", self.rpm)
        form.addRow("每批条数", self.batch_size)
        form.addRow("失败重试轮数", self.retries)
        return page

    def _cfpa_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.cfpa_url = QLineEdit(self.settings.cfpa_base_url)
        self.cfpa_hours = QDoubleSpinBox()
        self.cfpa_hours.setRange(0, 720)
        self.cfpa_hours.setSuffix(" 小时")
        self.cfpa_hours.setValue(self.settings.cfpa_check_hours)
        form.addRow("下载地址", self.cfpa_url)
        form.addRow("检查更新间隔", self.cfpa_hours)
        hint = QLabel("GitHub 访问慢时可以改成镜像地址，或设置 HTTPS_PROXY 环境变量。")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return page

    # ---- 术语表 ----
    def _glossary_tab(self) -> QWidget:
        page = QWidget()
        self.glossary = QTableWidget(0, 2)
        self.glossary.setHorizontalHeaderLabels(["英文", "中文"])
        self.glossary.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for en, zh in load_user_glossary(home_dir() / "glossary.tsv"):
            self.add_glossary_row(en, zh)
        add = QPushButton("添加一行")
        add.clicked.connect(lambda: self.add_glossary_row("", ""))
        remove = QPushButton("删除选中行")
        remove.clicked.connect(self._remove_glossary_rows)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("自定义术语优先级最高，翻译时会提供给模型，保证专有名词译法一致。"))
        layout.addWidget(self.glossary)
        layout.addLayout(buttons)
        return page

    def add_glossary_row(self, en: str, zh: str) -> None:
        r = self.glossary.rowCount()
        self.glossary.insertRow(r)
        self.glossary.setItem(r, 0, QTableWidgetItem(en))
        self.glossary.setItem(r, 1, QTableWidgetItem(zh))

    def _remove_glossary_rows(self) -> None:
        for r in sorted({i.row() for i in self.glossary.selectedIndexes()}, reverse=True):
            self.glossary.removeRow(r)

    def glossary_pairs(self) -> list[tuple[str, str]]:
        pairs = []
        for r in range(self.glossary.rowCount()):
            en, zh = self.glossary.item(r, 0), self.glossary.item(r, 1)
            if en and zh and en.text().strip() and zh.text().strip():
                pairs.append((en.text().strip(), zh.text().strip()))
        return pairs

    # ---- 配置文本规则 ----
    def _rules_tab(self) -> QWidget:
        page = QWidget()
        self.rules_edit = QPlainTextEdit()
        path = home_dir() / RULES_FILE
        self.rules_edit.setPlainText(path.read_text(encoding="utf-8") if path.is_file() else RULES_TEMPLATE)
        layout = QVBoxLayout(page)
        hint = QLabel("对所有整合包生效。单个整合包的规则可以写在 <实例目录>/.mctrans/config_rules.toml。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.rules_edit)
        return page

    # ---- 保存 ----
    def save(self) -> tuple[bool, str]:
        self._store_form()
        for name, p in self.providers.items():
            if not p.base_url or not p.model:
                return False, f"翻译服务 {PROVIDER_LABELS.get(name, name)} 缺少接口地址或模型名称"
        rules_text = self.rules_edit.toPlainText()
        _, problems = parse_rules(rules_text, "配置文本规则")
        if problems:
            return False, "\n".join(problems)
        s = self.settings
        s.providers = copy.deepcopy(self.providers)
        s.default_provider = self.default_box.currentData()
        s.concurrency = self.concurrency.value()
        s.requests_per_minute = self.rpm.value()
        s.batch_size = self.batch_size.value()
        s.max_retries = self.retries.value()
        s.cfpa_base_url = self.cfpa_url.text().strip()
        s.cfpa_check_hours = self.cfpa_hours.value()
        save_settings(s)
        save_user_glossary(home_dir() / "glossary.tsv", self.glossary_pairs())
        rules_path = home_dir() / RULES_FILE
        if rules_text.strip() and rules_text != RULES_TEMPLATE:
            rules_path.write_text(rules_text, encoding="utf-8")
        elif rules_path.is_file() and not rules_text.strip():
            rules_path.unlink()
        return True, "设置已保存"

    def accept(self) -> None:
        ok, message = self.save()
        if not ok:
            QMessageBox.warning(self, "无法保存", message)
            return
        super().accept()

    def done(self, result: int) -> None:
        if self.runner.busy:
            self.runner.wait()
        super().done(result)
