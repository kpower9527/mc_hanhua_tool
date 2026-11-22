# 麦块岛MC一键汉化

由 [麦块岛（www.maikuai.net）](https://www.maikuai.net) 出品的 Minecraft 整合包一键汉化工具。

把英文整合包（已安装的游戏实例目录）自动翻译成简体中文。当前支持 mod 语言文件、FTB Quests 任务书、Patchouli 手册、KubeJS 与 CraftTweaker 脚本中的提示文本，以及按规则指定的配置文本，支持 MC 1.12.2 与 1.16 及以上。

## 图形界面版（推荐）

1. 解压 `maikuai-hanhua-<版本>-windows.zip`，双击 `麦块岛MC一键汉化\麦块岛MC一键汉化.exe`
2. 点「设置」，选择翻译服务（DeepSeek 或通义千问）并填写 API Key，可以点「测试连接」确认
3. 点「添加整合包…」，选择实例目录（包含 `mods` 文件夹的那一层），会自动扫描
4. 在「概览」页点「翻译」（下拉菜单可以先试翻 50 条，或只翻某一类内容），完成后点「应用到游戏」
5. 在「审校」页可以筛选、搜索并直接修改译文，改完点「应用到游戏」生效；随时可以点「还原」撤销所有修改

同一目录下的 `maikuai-cli.exe` 是命令行版，用法与下文相同（把 `uv run mctrans` 换成 `maikuai-cli`）。

「更多 → 导出汉化补丁」生成的补丁附带 `一键安装汉化.bat`：玩家关闭游戏后双击（或把整合包文件夹拖到它上面），会复制补丁文件（覆盖前备份）、把汉化资源包排到资源包列表最上面，并把游戏语言设为简体中文。补丁目录本身不会被改动，可以反复运行，安装到其他整合包。

「设置 → 导出补丁」可以按「MC 版本 + 加载器」设置附加文件规则：导出时选出与整合包匹配的规则，把对应目录里的文件（例如 `mods/xxx.jar`）按相同目录结构放进补丁，一键安装时一并复制到整合包。版本留空匹配所有版本，`1.20` 匹配 1.20 和 1.20.x。

自己构建：`uv run python packaging/build.py`，产物为 `dist/maikuai-hanhua-<版本>-windows.zip`（解压前的目录在 `dist/<版本>/麦块岛MC一键汉化/`）。

## 命令行版安装

需要 [uv](https://docs.astral.sh/uv/)。

```
uv sync
```

图形界面也可以直接用 `uv run mctrans-gui` 启动。

## 配置 API Key

DeepSeek：设置环境变量 `DEEPSEEK_API_KEY`
通义千问（阿里云百炼）：设置环境变量 `DASHSCOPE_API_KEY`

也可以在 `~/.mctrans/config.toml` 中配置：

```toml
default_provider = "deepseek"   # 或 "qwen"
concurrency = 4                 # 并发请求数
requests_per_minute = 60        # 每分钟请求上限，0 为不限

[provider.deepseek]
api_key = "sk-..."
model = "deepseek-chat"

[provider.qwen]
model = "qwen-plus"
```

任何 OpenAI 兼容服务都可以加为自定义 provider（填写 `base_url`、`model`、`api_key`）。

## 使用

```
uv run mctrans run <实例目录> --set-lang     # 一键：扫描 → 翻译 → 应用
```

分步执行：

```
uv run mctrans scan <实例目录>               # 扫描，识别版本和 mod
uv run mctrans translate <实例目录> --dry-run # 预估费用
uv run mctrans translate <实例目录>           # 翻译（可中断，再次运行会续跑）
uv run mctrans apply <实例目录> --set-lang    # 生成资源包并启用
uv run mctrans restore <实例目录>             # 一键还原
uv run mctrans status <实例目录>              # 查看进度
```

### 译文来源与优先级

扫描时会按以下优先级为每条文本选择译文，只有都没有时才交给大模型翻译：

1. 人工确认的译文
2. mod 自带的中文
3. [CFPA](https://github.com/CFPAOrg/Minecraft-Mod-Language-Package) 社区人工译文（自动下载并缓存到 `~/.mctrans/cfpa/`，每 6 小时最多检查一次更新）
4. 翻译记忆库（`~/.mctrans/memory.db`，以前翻译过的文本跨整合包复用）
5. 大模型翻译

参数（`%s`、`{0}` 等）与原文不一致的 CFPA 译文会被视为过时并跳过。

```
uv run mctrans scan <实例目录> --offline   # 不联网，只用缓存的 CFPA 汉化包
uv run mctrans scan <实例目录> --no-cfpa   # 不使用 CFPA
```

GitHub 访问慢时，可以在配置中把 `cfpa_base_url` 改为镜像地址，或设置 `HTTPS_PROXY` 环境变量。

### 与 I18nUpdateMod 配合

如果整合包装了 I18nUpdateMod（自动汉化更新模组），工具会自动切换到配合模式：

- `maikuai-zh_cn-fill.zip`（补全包）：只包含 CFPA 没有的机翻，放在 I18nUpdateMod 资源包**之下**，以后 CFPA 补上人工译文时会自动覆盖机翻
- `maikuai-zh_cn-fix.zip`（修正包）：只包含人工确认的译文，放在 I18nUpdateMod 资源包**之上**

可以用 `--mode standalone` 或 `--mode i18nupdate` 强制指定模式。

### 任务书与手册

- **FTB Quests**：1.21 及以上版本生成 `config/ftbquests/quests/lang/zh_cn.snbt`（保留整合包原有中文，只补缺失的部分，兼容 FTB Quests Lang Splitter）；1.20.1 及更早版本直接替换章节文件里的文本，修改前自动备份
- **Patchouli 手册**：为没有中文的条目生成 `zh_cn` 文件，写入资源包；放在 `data/` 下的旧式手册暂不支持
- **Better Questing**（1.12.2 常见）：翻译 `config/betterquesting/DefaultQuests.json`（或 `DefaultQuests/` 目录）中的任务名称与描述。它只在新建存档或执行 `/bq_admin default load` 时载入，已有存档需要执行该命令
- **1.12.2**：支持 `.lang` 语言文件、CFPA 1.12.2 汉化包、旧版 FTB Quests（`config/ftbquests/normal/`）；资源包 ID 按 1.12 规则写入 `options.txt`

抽样试翻，先看看效果和费用：

```
uv run mctrans translate <实例目录> --only ftbquests --limit 50
```

注意：装有 Lang Splitter 时，游戏启动后会重写 `zh_cn.snbt`，此时 `restore` 会跳过这个文件（提示"应用后被改动过"），需要时手动删除即可。

### 脚本与配置文本

- **KubeJS**：翻译 `kubejs/*_scripts/` 中 `Text.of(...)`、`Text.red(...)` 等、`.displayName(...)`、`.tooltip(...)` 里的文字；`Text.translatable(...)` 与资源 ID 不会被改动
- **CraftTweaker**：翻译 `scripts/*.zs` 中 `format.xxx(...)`、`.addTooltip(...)`、`.addShiftTooltip(...)`、`.displayName = ...` 等处的文字
- **配置文本**：默认不处理。需要时在 `~/.mctrans/config_rules.toml`（全局）或 `<实例目录>/.mctrans/config_rules.toml`（单个整合包）中添加规则：

```toml
[[rule]]
glob = "config/loadingtips/*.txt"      # 相对实例目录的文件匹配
pattern = '^tip=(?P<text>.+)$'          # 多行正则，命名组 text 是要翻译的部分
```

以上文件修改前都会自动备份，可以用 `restore` 还原。

### 人工审校

```
uv run mctrans review export <实例目录> review.xlsx     # 导出机翻、待翻译和需复核的条目（--all 导出全部，--only 按类别）
# 用 Excel / WPS 修改「译文」列，或在「确认」列填「是」
uv run mctrans review import <实例目录> review.xlsx     # 导入，标记为人工确认
uv run mctrans apply <实例目录>                          # 重新应用使修改生效
```

人工确认的译文不会被自动覆盖，也会写入记忆库，在其他整合包中直接复用。格式码（`§a`、`%s`、`{0}`、`$(item)`、`&6` 等）与原文不一致的行会被拒绝导入。

### 导出补丁分发

```
uv run mctrans export-patch <实例目录> <输出目录>
```

生成与实例目录结构相同的补丁（资源包、修改过的任务书和脚本等）以及使用说明，不修改实例本身。包含 CFPA 社区译文时请遵守 CC BY-NC-SA 4.0（署名、非商业、相同方式共享）。

### 实例目录

实例目录是包含 `mods/` 的那一层，例如 HMCL/PCL 开启版本隔离时的 `.minecraft/versions/<版本名>/`，或 Prism 的 `instances/<实例名>/minecraft/`。版本识别失败时用 `--mc-version 1.20.1` 指定。

## 自定义术语表

在 `~/.mctrans/glossary.tsv` 中每行写一条 `英文<Tab>中文`，优先级高于自动提取的术语。

## 说明

- 工作数据保存在实例目录下的 `.mctrans/`，分发整合包时请排除这个目录
- mod 自带的中文会被直接采用，不会重复翻译
- 独立模式生成 `resourcepacks/maikuai-zh_cn.zip`，放在资源包列表最上层
