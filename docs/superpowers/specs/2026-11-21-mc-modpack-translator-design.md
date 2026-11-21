# MC 整合包自动汉化工具（mctrans）设计文档

- 日期：2026-11-21
- 状态：设计已确认，待编写实施计划

## 1. 目标与范围

把英文 Minecraft 整合包（已安装的实例目录）自动翻译成简体中文。

### 1.1 翻译范围

| 内容 | 说明 |
|---|---|
| mod 语言文件 | jar 内 `assets/<ns>/lang/en_us.json`（第二阶段支持 `.lang`） |
| 任务书 | FTB Quests（新旧两种格式）；第二阶段支持 Better Questing |
| Patchouli 手册 | jar 内及实例目录内的手册 |
| KubeJS 脚本 | `kubejs/` 下的脚本和资源目录 |
| CraftTweaker 脚本 | `scripts/*.zs` |
| 配置文本 | 按规则文件匹配的配置字段（加载提示、主菜单文本等） |

### 1.2 关键决定

| 项目 | 决定 |
|---|---|
| 译文来源 | 优先使用 CFPA 人工译文，缺失部分用 LLM 翻译；CFPA 同时作为术语表 |
| 翻译引擎 | OpenAI 兼容接口，内置 DeepSeek 和通义千问（DashScope）预设 |
| 形式 | 先做 CLI 核心，之后在上面加界面（倾向本地 Web 界面） |
| 输入 | 已安装的游戏实例目录（PCL / HMCL / Prism 等启动器的版本目录） |
| 输出 | 直接应用（带备份与还原）+ 导出补丁目录 |
| MC 版本 | 第一阶段 1.16+；第二阶段 1.12.2 |
| 开发语言 | Python |
| 审校 | 翻译记忆库 + Excel 导出导入；以后加 Web 审校 |
| I18nUpdateMod | 自动检测，按检测结果在"配合模式"与"独立模式"之间切换 |

### 1.3 不在本期范围

- 整合包压缩文件（CurseForge `.zip` / Modrinth `.mrpack`）作为输入
- 图形界面 / Web 界面
- 传统机翻 API（DeepL、百度等）和本地模型的专门适配（OpenAI 兼容的本地服务可直接通过配置使用）
- 把翻译结果提交回 CFPA 仓库（CFPA 禁止未经修改的机翻提交）

## 2. 整体架构

采用**分阶段流水线 + 统一中间层**：

```
扫描(scan) → 提取(extract) → 匹配(match) → 机翻(translate) → 审校(review) → 写回(apply / export-patch)
```

- 每个阶段的结果都写入 SQLite，可以单独重跑，中断后能续跑
- 格式处理器只负责"文件 ↔ 条目"的转换，不碰翻译；翻译模块只处理条目，不碰文件
- 新增格式只需增加一个处理器

### 2.1 模块划分

```
mctrans/
├── cli.py              # 命令行入口（typer）
├── config.py           # 配置加载（toml）：API Key、模型预设、并发、单价等
├── instance.py         # 实例扫描：MC 版本、加载器、mod 列表、I18nUpdateMod 检测
├── store.py            # SQLite 中间层：条目、译文、运行记录；全局记忆库
├── cfpa/               # CFPA 资源包下载、缓存、索引、术语表构建
├── handlers/
│   ├── base.py         # 统一接口：detect() / extract() / write()
│   ├── lang_json.py    # mod 语言文件 .json
│   ├── lang_legacy.py  # .lang（第二阶段）
│   ├── ftbquests.py    # FTB Quests
│   ├── betterquesting.py # Better Questing（第二阶段）
│   ├── patchouli.py    # Patchouli 手册
│   ├── kubejs.py       # KubeJS 脚本
│   ├── crafttweaker.py # CraftTweaker 脚本
│   └── config_text.py  # 配置文本（规则驱动）
├── translate/          # LLM 客户端、分批、格式码保护、术语注入、校验
├── review.py           # Excel 导出 / 导入
└── output/             # 资源包生成、直接应用 + 备份还原、补丁导出
```

### 2.2 命令

```
mctrans scan <实例目录>          # 扫描并提取条目到工作区数据库
mctrans translate [--dry-run]   # CFPA 匹配 + LLM 翻译（可中断续跑；dry-run 输出成本预估）
mctrans review export <文件>     # 导出审校表格
mctrans review import <文件>     # 导入审校结果（标记为人工确认）
mctrans apply [--dry-run] [--set-lang] [--mode auto|i18nupdate|standalone]
mctrans restore                 # 一键还原
mctrans export-patch <目录>      # 导出补丁目录
mctrans run <实例目录>           # 一键全流程（scan → translate → apply）
```

### 2.3 存储位置

- **工作区**：`<实例目录>/.mctrans/`，存放 `workspace.db`、备份、日志
- **全局目录**：`~/.mctrans/`，存放全局翻译记忆库 `memory.db`、CFPA 缓存、用户配置和自定义术语表，跨整合包复用

## 3. 数据模型

### 3.1 `entries`：待翻译条目

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `handler` | 处理器名，如 `lang_json`、`ftbquests` |
| `source` | 来源文件，如 `mods/create-1.20.1.jar!/assets/create/lang/en_us.json` |
| `locator` | 文件内定位，如语言键 `block.create.cogwheel`，或 `chapters/intro.snbt#quests[3].description[1]` |
| `namespace` | modid |
| `original` | 英文原文 |
| `context` | 辅助翻译的上下文（任务标题、章节、相邻键等） |
| `existing_zh` | 文件中已存在的中文（如 mod 自带 zh_cn） |
| `file_hash` | 来源文件哈希，用于增量检测 |
| `stale` | 重新扫描时条目已消失则置为真（不删除） |

唯一约束：`(handler, source, locator)`。

### 3.2 `translations`：当前采用的译文

| 字段 | 说明 |
|---|---|
| `entry_id` | 关联条目 |
| `text` | 译文 |
| `origin` | `mod_builtin` / `cfpa` / `memory` / `llm` / `human` |
| `status` | `pending` / `done` / `failed` / `confirmed` |
| `model` | 机翻所用模型 |
| `note` | 校验警告、"原文已变更需复核"等标记 |
| `updated_at` | 更新时间 |

### 3.3 `runs`

记录每次运行的工具版本、参数和统计数据。

### 3.4 全局记忆库 `~/.mctrans/memory.db`

以 `(namespace, sha256(原文))` 为键，保存译文、来源和是否人工确认。

### 3.5 译文优先级

```
human（人工确认） > mod_builtin > cfpa > memory > llm（新机翻）
```

`confirmed` 状态的译文不会被任何自动流程覆盖。

### 3.6 增量更新规则

重新 `scan` 时：
- `locator` 相同且原文未变：保留原译文
- 原文改变：状态重置为 `pending`；如果原来是人工确认，在 `note` 中标记"原文已变更，需复核"，并在审校表格中突出显示
- 条目消失：置 `stale`，不删除

## 4. CFPA 集成

数据源：[CFPAOrg/Minecraft-Mod-Language-Package](https://github.com/CFPAOrg/Minecraft-Mod-Language-Package)（CC BY-NC-SA 4.0）。

- 下载对应版本的汉化资源包（GitHub Releases，或 cfpa.site），缓存在 `~/.mctrans/cfpa/`，按发布时间判断是否需要更新
- MC 版本映射到 CFPA 版本目录：例如 1.20.1 → `1.20`，Fabric 加载器使用 `-fabric` 后缀版本（不存在时回退到不带后缀的版本）
- 建立索引：`(namespace, key) → 中文译文`，资源包内若含英文原文也一并索引
- **匹配规则**：按 namespace + 键匹配。CFPA 中的英文与当前 mod 原文不一致时不直接采用，而是作为参考译文交给 LLM 重新翻译
- **术语表**：从 CFPA 全部语言文件中提取 `item.*`、`block.*`、`entity.*` 等键的中英对照，构建全局术语表

## 5. LLM 翻译

### 5.1 模型预设

```toml
[provider.deepseek]
base_url = "https://api.deepseek.com"
model = "deepseek-chat"

[provider.qwen]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = "qwen-plus"
```

使用 `openai` Python SDK。API Key 从环境变量（`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`）或配置文件读取。模型名、单价可在配置中修改。

### 5.2 去重与分批

- 完全相同的原文（同一 namespace 内）只翻译一次，译文回填到所有出现位置
- 同一 namespace、同一文件的条目放在同一批，每批约 30–50 条，并受 token 上限约束
- 请求与响应都用带编号的 JSON：`{"1": "原文", ...}` → `{"1": "译文", ...}`；启用 `response_format={"type": "json_object"}`
- system prompt 说明：Minecraft 语境、mod 名称、内容类型（物品名 / 任务描述 / 手册正文）、保留占位符、输出格式

### 5.3 格式码保护

发送前将格式码替换为占位符（如 `⟦0⟧`），返回后还原：
- `§x` 颜色与样式码、`&x`
- `%s`、`%d`、`%1$s`、`%%`、`{0}`、`{player}` 等参数
- Patchouli 宏 `$(...)`、`$()`
- 换行 `\n`
- 富文本 JSON 组件：只翻译其中的 `text` 字段

### 5.4 术语注入

- 术语来源（优先级从高到低）：用户自定义术语表 > 当前整合包已有的物品名译文（mod 自带 + CFPA）> CFPA 全局术语表
- 每批只注入在本批原文中出现的术语（大小写不敏感的整词匹配，长词优先）

### 5.5 结果校验

- 编号完整，无漏条、无多余条目
- 占位符数量与种类与原文一致
- 译文非空
- 原文含字母但译文与原文完全相同：仅标记可疑，不强制重试

校验失败的条目单独重试（最多 3 次），仍失败则标记 `failed`。

### 5.6 并发与容错

- 可配置并发数与每分钟请求上限
- 429 / 5xx / 超时按指数退避重试
- 每批完成后立即写入数据库；再次运行 `translate` 只处理未完成的条目

### 5.7 成本预估

`translate --dry-run` 输出待翻译条目数、去重后条目数、预估输入/输出 token 数，以及按配置单价计算的预估费用。

## 6. 格式处理器

统一接口：

```python
class Handler:
    name: str
    def detect(self, instance) -> bool: ...
    def extract(self, instance) -> Iterable[Entry]: ...
    def write(self, items: list[tuple[Entry, Translation]], target: OutputTarget) -> None: ...
```

### 6.1 mod 语言文件 `lang_json`

- 遍历 `mods/*.jar`，读取 `assets/<ns>/lang/en_us.json`（文件名大小写不敏感），同时读取 jar 内已有的 `zh_cn.json` 作为 `mod_builtin`
- 递归处理 jar-in-jar（`META-INF/jars/*.jar`）
- 也处理实例内资源目录（如 `kubejs/assets/`）下的语言文件
- 写回：生成资源包中的 `assets/<ns>/lang/zh_cn.json`，不修改 jar

### 6.2 FTB Quests `ftbquests`

- **旧版**（1.16–1.20.1）：`config/ftbquests/quests/` 下的 `chapters/*.snbt`、`chapter_groups.snbt`、`reward_tables/*.snbt` 等，提取 `title`、`subtitle`、`description` 等文本字段；`{key}` 形式的翻译键引用跳过
- **新版**（1.20.4+/1.21）：读取 `quests/lang/en_us.snbt`，生成 `zh_cn.snbt`，原文件不动
- 写回方式：自研 SNBT 解析器在解析时记录每个字符串字面量的字节区间，写回时只替换对应区间，其余内容保持字节级一致

### 6.3 Patchouli 手册 `patchouli`

- 位置：jar 内 `assets/<ns>/patchouli_books/<book>/en_us/`；实例内 `patchouli_books/`；KubeJS 资源目录
- 翻译 categories / entries / templates 中的 `name`、`title`、`text`、`description`、`landing_text` 及 pages 内的文本字段
- 写回：生成对应的 `zh_cn/` 目录树（资源包内或实例目录内，取决于验证结果，见第 9 节）

### 6.4 KubeJS 脚本 `kubejs`

- 只翻译白名单 API 调用中的字符串字面量，例如 `.displayName(...)`、`Text.of(...)`、`Component.literal(...)`、`tooltip.add(...)`、`.tooltip(...)`
- 使用轻量 JS 词法扫描器定位字符串，跳过注释、正则字面量和模板字符串插值部分
- 定点替换写回；白名单可通过用户配置扩展

### 6.5 CraftTweaker 脚本 `crafttweaker`

- 白名单规则：`.displayName = "..."`、`.addTooltip("...")`、`.addShiftTooltip("...")` 等
- 词法扫描 + 定点替换，与 KubeJS 共用扫描基础设施

### 6.6 配置文本 `config_text`

- 规则格式：文件 glob + 字段路径（JSON/TOML/SNBT）或正则
- 内置常见规则（加载提示、主菜单类 mod 文本等），用户可在 `~/.mctrans/rules/` 补充
- 只处理有规则覆盖的文件

### 6.7 第二阶段（1.12.2）

- `lang_legacy`：`.lang` 格式（`key=value`，注意注释与转义）
- `betterquesting`：`config/betterquesting/DefaultQuests.json` 及拆分格式
- 旧版 FTB Quests（1.12.2 的格式）

## 7. 输出

### 7.1 I18nUpdateMod 双模式

扫描时检测 `mods/` 中是否存在 I18nUpdateMod，自动选择模式（可用 `--mode` 强制指定）。

| 模式 | 资源包 | 内容 | 位置 |
|---|---|---|---|
| 配合模式（检测到 I18nUpdateMod） | 补全包 | `llm` / `memory` 来源的条目 | CFPA 包之下 |
| | 修正包 | 所有 `human` 来源（人工确认）的条目 | CFPA 包之上 |
| 独立模式（未检测到） | 完整包 | 所有译文（含 CFPA） | 最上层 |

配合模式下，`cfpa` 和 `mod_builtin` 来源的条目不进入任何包。这样以后 CFPA 补充了人工译文时，会自动覆盖我们的机翻。

### 7.2 直接应用 `apply`

- 资源包写入 `resourcepacks/`，`pack.mcmeta` 的 `pack_format` 按 MC 版本自动填写
- 修改 `options.txt` 的 `resourcePacks` 列表，按 7.1 的顺序插入；`--set-lang` 时同时设置 `lang:zh_cn`
- 被直接修改的文件（任务书、脚本、配置）写入前备份到 `.mctrans/backups/<运行ID>/`，并记录清单：原路径、备份路径、写入后哈希
- 每个写出的文件重新解析校验，失败则回滚该文件

### 7.3 还原 `restore`

- 按清单恢复被修改的文件，删除生成的资源包，还原 `options.txt`
- 若文件在 apply 之后被改动（哈希不一致），提示并跳过该文件

### 7.4 导出补丁 `export-patch`

- 输出与实例结构相同的目录：`resourcepacks/` 及被修改的文件
- 附带说明文件：使用方法、署名信息；包含 CFPA 内容时附 CC BY-NC-SA 4.0 声明

## 8. 审校

- `review export` 导出 Excel（openpyxl），列：ID、处理器、文件、定位、原文、译文、来源、状态、备注
- 支持按处理器 / 来源 / 状态筛选导出（例如只导出机翻和需复核的条目）
- `review import` 按 ID 导入；译文有改动或用户在"确认"列打勾的条目标记为 `human` + `confirmed`，并同步写入全局记忆库
- 以后的 Web 审校界面直接读写同一套数据

## 9. 实现前的验证项

1. I18nUpdateMod 在各版本中的资源包命名、是否会强制调整 `options.txt` 的加载顺序、缓存位置。若无法稳定控制顺序，退化为提示用户手动排序
2. Patchouli 各版本是否从资源包读取手册内容；不读取时改为写入实例目录
3. 新版 FTB Quests 语言文件的确切路径与格式，以及从哪个版本开始支持
4. CFPA 资源包的发布文件名格式与获取方式

## 10. 错误处理

- 单个文件解析失败不中断流程，记录日志，结束时汇总报告
- API 错误自动重试，最终失败的条目保持 `failed`，下次运行继续
- 所有写操作支持 `--dry-run`
- 运行日志写入 `.mctrans/logs/`

## 11. 测试策略（pytest）

- **往返测试**：每个处理器 `extract` 后以原文作为译文 `write`，结果必须与原文件字节一致
- **格式码保护**：各种格式码组合的保护与还原
- **夹具**：仓库内放置自制的小型样例（迷你 jar、SNBT、Patchouli 手册、KubeJS/ZS 脚本），不包含真实 mod 文件
- **翻译模块**：模拟 LLM 返回，覆盖漏条、占位符丢失、非法 JSON、429 等异常
- **端到端**：用假实例目录跑 `run → restore`，验证还原后与原始状态完全一致
- **集成测试**：设置 API Key 时才执行的真实调用测试
- **手动验证**：用一个真实整合包完整跑一遍

## 12. 技术选型

- Python 3.11+
- `typer`（CLI）、`openai`（LLM 客户端）、`openpyxl`（Excel）、`httpx`（下载）、`rich`（进度显示）
- 标准库：`sqlite3`、`zipfile`、`tomllib`、`json`
- 自研：SNBT 解析器（带位置记录）、JS/ZS 轻量词法扫描器
- 分发：PyInstaller 打包为 exe（后期）
