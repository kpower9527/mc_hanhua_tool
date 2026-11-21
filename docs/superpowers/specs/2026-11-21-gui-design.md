# mctrans 图形界面（桌面版 exe）设计文档

- 日期：2026-11-21
- 状态：设计已确认（框架、范围、分发形式由用户选定，其余细节由开发者决定）
- 前置：`2026-11-21-mc-modpack-translator-design.md`（命令行版全部计划已完成）

## 1. 目标与决定

| 项目 | 决定 |
|---|---|
| 形式 | 桌面图形界面，不做网页 |
| 框架 | PySide6（Qt 6，LGPL） |
| 范围 | 完整功能一次做完：一键流程、内置审校、设置、补丁导出、多整合包管理 |
| 分发 | PyInstaller 文件夹版，打成 zip；内含 `mctrans.exe`（界面）与 `mctrans-cli.exe`（命令行） |

## 2. 架构

```
mctrans/
├── service.py        # 新增：服务层（界面与命令行共用），Reporter 回调、可取消
├── settings_io.py    # 新增：config.toml 的写入（tomllib 只能读）
├── instances.py      # 新增：最近使用的整合包列表（~/.mctrans/instances.json）
├── cli.py            # 改为调用 service
└── gui/
    ├── app.py        # 入口：创建 QApplication、主窗口
    ├── worker.py     # QThread 封装：在后台运行服务层函数，信号回传日志/进度/结果
    ├── main_window.py# 主窗口：左侧整合包列表 + 右侧页签
    ├── overview.py   # 概览页：信息、统计、操作按钮、进度、日志
    ├── review.py     # 审校页：表格模型、筛选代理、详情编辑区、批量操作
    └── settings.py   # 设置窗口（五个页签）
```

- **服务层**把 `cli.py` 中的流程（扫描、预估、翻译、应用、还原、导出补丁、审校导入导出、实例统计）抽成返回结构化结果的函数，通过 `Reporter`（`log(text, level)`、`progress(done, total)`、`cancelled() -> bool`）报告进度。命令行用打印到控制台的 Reporter，界面用发 Qt 信号的 Reporter
- **翻译可取消**：`run_translation` 增加 `should_stop` 回调，取消后不再提交新批次，已完成部分保存，剩余条目保持待翻译
- **线程**：所有耗时操作在 `QThread` 中执行；每个线程使用自己的 SQLite 连接（SQLite 连接不能跨线程共享）；同一时间每个整合包只允许一个后台任务
- **数据**：沿用实例下 `.mctrans/` 与全局 `~/.mctrans/`，命令行与界面可以混用

## 3. 界面

### 3.1 主窗口
- 左侧：整合包列表（名称、MC 版本、翻译进度百分比），添加（选择文件夹，自动识别版本；识别失败时弹框让用户填写版本）/ 移除（只从列表移除）
- 右侧：页签"概览""审校"；右上角"设置"按钮
- 窗口标题显示版本号

### 3.2 概览页
- 信息：路径、MC 版本、加载器、是否检测到 I18nUpdateMod、上次扫描时间
- 统计表：各类别的条目数（已完成 / 待翻译 / 失败）与译文来源分布
- 费用预估：按当前设置的翻译服务计算
- 按钮：扫描、翻译（下拉：全部 / 按类别 / 试翻 50 条）、应用（含"同时设为简体中文"勾选、模式选择）、还原、更多（导出补丁、导出 Excel、导入 Excel、打开实例目录）
- 进度条（含预计剩余时间）与取消按钮；日志区（警告标黄，错误标红）
- 未扫描的整合包只显示"扫描"按钮

### 3.3 审校页
- `QTableView` + 自定义 `QAbstractTableModel`（一次加载全部条目）+ `QSortFilterProxyModel`
- 列：类别、文件、原文、译文（可编辑）、来源、状态
- 筛选：类别、来源、状态（默认"需审校"，与 Excel 导出默认范围一致）、搜索框（原文与译文）
- 详情区：完整原文（只读）与译文编辑框；实时显示格式码校验结果（一致 / 缺少哪些）
- 保存：格式码一致才保存，记为人工确认并写入记忆库
- 批量：确认选中、重新机翻选中（后台执行）
- 底部提示"有 N 处修改未应用"与"应用"按钮

### 3.4 设置窗口
- 翻译服务：默认服务；DeepSeek / 通义千问 / 自定义服务的地址、模型、API Key（密码框）、单价；"测试连接"按钮
- 运行参数：并发数、每分钟请求上限、每批条数、重试次数
- CFPA：下载地址、检查间隔（小时）
- 术语表：两列表格编辑，保存到 `glossary.tsv`
- 配置文本规则：文本编辑，保存前校验（复用 `load_rules` 的检查逻辑）

## 4. 打包

- `packaging/mctrans.spec`：两个 EXE（界面版 `console=False`、命令行版 `console=True`）共享一个 COLLECT 目录
- 排除不用的 Qt 模块（WebEngine、Multimedia、3D、Qml 等）以减小体积
- `packaging/build.py`：调用 PyInstaller，生成 `dist/mctrans/`，再打包为 `dist/mctrans-<版本>-windows.zip`
- 冒烟测试：`mctrans-cli.exe --help` 正常输出；`mctrans.exe --smoke-test` 打开主窗口后自动退出，返回码 0

## 5. 测试

- 服务层：普通单元测试（沿用 FakeClient、假 CFPA 下载）
- 界面：pytest-qt，`QT_QPA_PLATFORM=offscreen`；覆盖审校表格模型（数据、编辑、格式码校验、筛选）、设置读写、整合包列表、主窗口一键流程（扫描→翻译→应用，使用 FakeClient）
- 现有命令行测试全部保持通过
