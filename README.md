# mctrans：Minecraft 整合包自动汉化工具

把英文整合包（已安装的游戏实例目录）自动翻译成简体中文。当前版本支持 mod 语言文件，MC 1.16 及以上。

## 安装

需要 [uv](https://docs.astral.sh/uv/)。

```
uv sync
```

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

实例目录是包含 `mods/` 的那一层，例如 HMCL/PCL 开启版本隔离时的 `.minecraft/versions/<版本名>/`，或 Prism 的 `instances/<实例名>/minecraft/`。版本识别失败时用 `--mc-version 1.20.1` 指定。

## 自定义术语表

在 `~/.mctrans/glossary.tsv` 中每行写一条 `英文<Tab>中文`，优先级高于自动提取的术语。

## 说明

- 工作数据保存在实例目录下的 `.mctrans/`，分发整合包时请排除这个目录
- mod 自带的中文会被直接采用，不会重复翻译
- 生成的资源包为 `resourcepacks/mctrans-zh_cn.zip`，放在资源包列表最上层
