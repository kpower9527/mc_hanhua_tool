"""导出补丁附带的一键安装脚本（Windows）。

玩家双击「一键安装汉化.bat」（或把一个或多个整合包文件夹拖到它上面）：
1. 补丁已解压在整合包目录里时，原地启用；否则选择整合包目录，把补丁文件复制过去（覆盖前备份）。
   脚本不改动补丁目录本身，可以反复运行（每次装一个整合包，也可以一次拖入多个文件夹）。
2. 修改 options.txt：把汉化资源包放到资源包列表最上面（配合 I18nUpdateMod 时补全包放在 CFPA 包下面），
   语言设为简体中文
bat 只含 ASCII，真正的逻辑在 UTF-8（带 BOM）的 ps1 里，避免 cmd 代码页导致中文乱码。
"""
from __future__ import annotations

from ..brand import APP_NAME, SITE_URL

BAT_NAME = "一键安装汉化.bat"
PS1_NAME = "install-hanhua.ps1"

BAT = (
    "@echo off\r\n"
    'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0' + PS1_NAME + '" %*\r\n'
    "pause\r\n"
)

# 补丁目录里自带 mods（例如附加的 mod）时，靠这些「玩过的整合包才有」的文件区分「补丁解压在整合包里」
INSTANCE_MARKERS = ("options.txt", "saves", "logs", "crash-reports", "servers.dat", "versions")


def _ps_list(items: list[str]) -> str:
    return "@(" + ", ".join("'" + s.replace("'", "''") + "'" for s in items) + ")"


def _ps_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def install_script(top: list[str], anchor: str, below: list[str], above: list[str],
                   defaults: list[str], skip: list[str], shipped_jars: int = 0) -> str:
    """top：独立模式放到最上面的包；anchor/below/above：配合 I18nUpdateMod 时围绕 CFPA 包放置。

    shipped_jars：补丁 mods 文件夹里自带的 jar 数量，用于判断补丁目录是否就是整合包目录。
    """
    ours = top + below + above
    return f"""# {APP_NAME} - 汉化补丁安装脚本  {SITE_URL}
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Targets)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Top = {_ps_list(top)}
$Anchor = {_ps_str(anchor)}
$Below = {_ps_list(below)}
$Above = {_ps_list(above)}
$Ours = {_ps_list(ours)}
$Defaults = {_ps_list(defaults)}
$Skip = {_ps_list(skip)}
$Markers = {_ps_list(list(INSTANCE_MARKERS))}
$ShippedJars = {shipped_jars}

Write-Host '{APP_NAME} - 安装汉化补丁'
Write-Host '安装前请先关闭游戏（游戏退出时会改写资源包设置）。'
Write-Host ''

function Test-Instance([string]$Dir) {{ return ($Dir -ne '') -and (Test-Path -LiteralPath (Join-Path $Dir 'mods')) }}

# 补丁是否直接解压在整合包目录里（补丁自己也可能带 mods 文件夹，不能只看 mods）
function Test-InPlace([string]$Dir) {{
    if (-not (Test-Instance $Dir)) {{ return $false }}
    foreach ($m in $Markers) {{ if (Test-Path -LiteralPath (Join-Path $Dir $m)) {{ return $true }} }}
    $jars = @(Get-ChildItem -LiteralPath (Join-Path $Dir 'mods') -Filter '*.jar' -File -ErrorAction SilentlyContinue)
    return $jars.Count -gt ($ShippedJars + 5)
}}

function Select-Instance([string]$Start) {{
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = '请选择要安装汉化的整合包目录（包含 mods 文件夹的那一层）'
    if ($Start -ne '') {{ $dlg.SelectedPath = $Start }}
    # 置顶的隐藏窗口作为父窗口，避免选择框被控制台挡住
    $owner = New-Object System.Windows.Forms.Form -Property @{{ TopMost = $true; ShowInTaskbar = $false }}
    try {{
        if ($dlg.ShowDialog($owner) -ne [System.Windows.Forms.DialogResult]::OK) {{ return '' }}
        return $dlg.SelectedPath
    }} finally {{ $owner.Dispose() }}
}}

function Install-Patch([string]$Target) {{
    $Target = (Resolve-Path -LiteralPath $Target).Path.TrimEnd('\\')
    if (-not (Test-Instance $Target)) {{
        Write-Host "所选目录下没有 mods 文件夹：$Target"
        Write-Host '请选择包含 mods、config 文件夹的那一层目录。'
        return $false
    }}
    Write-Host "安装到：$Target"
    if ($Target -ne $Here) {{
        $backup = Join-Path $Target ('.hanhua-backup\\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        $count = 0
        Get-ChildItem -LiteralPath $Here -Recurse -File | ForEach-Object {{
            $rel = $_.FullName.Substring($Here.Length).TrimStart('\\')
            if ($Skip -contains $rel) {{ return }}
            $dest = Join-Path $Target $rel
            if (Test-Path -LiteralPath $dest) {{
                $bak = Join-Path $backup $rel
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bak) | Out-Null
                Copy-Item -LiteralPath $dest -Destination $bak -Force
            }}
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
            $count++
        }}
        Write-Host "已复制 $count 个文件"
        if (Test-Path -LiteralPath $backup) {{ Write-Host "被覆盖的原文件已备份到 $backup" }}
    }}

    $opts = Join-Path $Target 'options.txt'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $raw = ''
    if (Test-Path -LiteralPath $opts) {{ $raw = [System.IO.File]::ReadAllText($opts, $utf8) }}
    $nl = "`n"
    if ($raw.Contains("`r`n")) {{ $nl = "`r`n" }}
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($l in ($raw -split "`r?`n")) {{ if ($l -ne '') {{ $lines.Add($l) }} }}

    $idx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {{ if ($lines[$i].StartsWith('resourcePacks:')) {{ $idx = $i; break }} }}
    $packs = New-Object System.Collections.Generic.List[string]
    if ($idx -ge 0) {{
        # Windows PowerShell 5.1 的 ConvertFrom-Json 把整个数组作为一个对象输出，不能再套 @()
        try {{ $parsed = ConvertFrom-Json $lines[$idx].Substring(14) }} catch {{ $parsed = $null }}
        foreach ($p in $parsed) {{ if ($p -is [string]) {{ $packs.Add($p) }} }}
    }} else {{
        foreach ($p in $Defaults) {{ $packs.Add($p) }}
    }}
    $rest = New-Object System.Collections.Generic.List[string]
    foreach ($p in $packs) {{ if ($Ours -notcontains $p) {{ $rest.Add($p) }} }}
    $result = New-Object System.Collections.Generic.List[string]
    if ($Anchor -ne '') {{
        if (-not $rest.Contains($Anchor)) {{ $rest.Add($Anchor) }}
        foreach ($p in $rest) {{
            if ($p -eq $Anchor) {{
                foreach ($b in $Below) {{ $result.Add($b) }}
                $result.Add($p)
                foreach ($a in $Above) {{ $result.Add($a) }}
            }} else {{ $result.Add($p) }}
        }}
    }} else {{
        foreach ($p in $rest) {{ $result.Add($p) }}
        foreach ($p in $Top) {{ $result.Add($p) }}
    }}
    $items = foreach ($p in $result) {{ '"' + $p.Replace('\\', '\\\\').Replace('"', '\\"') + '"' }}
    $values = @{{ 'resourcePacks' = '[' + ($items -join ',') + ']'; 'lang' = 'zh_cn' }}
    foreach ($key in @('resourcePacks', 'lang')) {{
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {{
            if ($lines[$i].StartsWith($key + ':')) {{ $lines[$i] = $key + ':' + $values[$key]; $found = $true; break }}
        }}
        if (-not $found) {{ $lines.Add($key + ':' + $values[$key]) }}
    }}
    [System.IO.File]::WriteAllText($opts, (($lines -join $nl) + $nl), $utf8)
    Write-Host '已启用汉化资源包（放在资源包列表最上面），游戏语言已设为简体中文。'
    Write-Host ''
    return $true
}}

$ok = 0
$failed = 0
if ($Targets) {{
    # 拖放或命令行：可以一次给多个整合包目录
    foreach ($t in $Targets) {{
        if ($t -eq '') {{ continue }}
        if (Install-Patch $t) {{ $ok++ }} else {{ $failed++ }}
    }}
}} elseif (Test-InPlace $Here) {{
    if (Install-Patch $Here) {{ $ok++ }} else {{ $failed++ }}
}} else {{
    $t = Select-Instance ''
    if ($t -ne '') {{ if (Install-Patch $t) {{ $ok++ }} else {{ $failed++ }} }}
}}

if ($ok -eq 0 -and $failed -eq 0) {{ Write-Host '已取消，没有安装到任何整合包。'; exit 1 }}
Write-Host "完成：成功安装 $ok 个整合包$(if ($failed) {{ "，失败 $failed 个" }})。"
Write-Host '启动游戏即可看到中文。更多整合包与汉化：{SITE_URL}'
if ($failed) {{ exit 1 }}
"""
