# agent-pipeline Windows 引导安装：一条命令从零跑起来。
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# 幂等：可重复运行；已配置的项会以当前值作默认。

$ErrorActionPreference = "Stop"

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Dir
$Src = Join-Path $Dir "src"

$Py = Join-Path $Dir ".venv\Scripts\python.exe"
$Pip = Join-Path $Dir ".venv\Scripts\pip.exe"
$PypiMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"

Write-Host "═══════════════════════════════════════"
Write-Host " agent-pipeline Windows 引导安装"
Write-Host " 目录: $Dir"
Write-Host "═══════════════════════════════════════"

# ── .env 工具 ────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

function Get-DotEnvValue {
    param([string]$Key)
    if (-not (Test-Path ".env")) { return "" }
    $prefix = "$Key="
    $line = Get-Content ".env" -Encoding UTF8 | Where-Object { $_.StartsWith($prefix) } | Select-Object -First 1
    if (-not $line) { return "" }
    return $line.Substring($prefix.Length)
}

function Test-PlaceholderValue {
    param([string]$Value)
    return [string]::IsNullOrWhiteSpace($Value) -or $Value -in @("cli_xxx", "xxx", "app_token_xxx", "tblxxx", "/abs/path/to/your/repo")
}

function Set-DotEnvValue {
    param([string]$Key, [string]$Value)
    $lines = @()
    if (Test-Path ".env") {
        $prefix = "$Key="
        $lines = @(Get-Content ".env" -Encoding UTF8 | Where-Object { -not $_.StartsWith($prefix) })
    }
    $lines += "$Key=$Value"
    Set-Content ".env" -Value $lines -Encoding UTF8
}

function Ask-DotEnv {
    param([string]$Key, [string]$Prompt, [string]$Default = $null)
    $current = Get-DotEnvValue $Key
    if (Test-PlaceholderValue $current) {
        $current = ""
    }
    if ($null -eq $Default -or $Default -eq "") {
        $Default = $current
    }
    $value = Read-Host "  $Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = $Default
    }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        Set-DotEnvValue $Key $value
    }
}

function Ensure-Workspaces {
    if (Test-Path "workspaces.json") {
        return
    }
    $repo = Get-DotEnvValue "PIPELINE_REPO_PATH"
    if (Test-PlaceholderValue $repo) {
        return
    }
    $repoName = Split-Path -Leaf $repo
    if ([string]::IsNullOrWhiteSpace($repoName)) {
        $repoName = "default"
    }
    $items = [ordered]@{}
    $items[$repoName] = [ordered]@{
        path = $repo
        scm = "git"
        base = "origin/main"
        test_cmd = ""
    }
    $data = [ordered]@{
        default = $repoName
        items = $items
    }
    $json = $data | ConvertTo-Json -Depth 8
    Set-Content "workspaces.json" -Value $json -Encoding UTF8
    Write-Host "  ✓ 已生成 workspaces.json（默认工作区：$repoName）"
}

function Find-BasePython {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @($python.Source) }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { return @($pyLauncher.Source, "-3") }
    throw "未找到 Python。请先安装 Python 3，并确认 python 或 py 在 PATH 中。"
}

# ── 1. venv + 依赖 ───────────────────────────────────────
if (-not (Test-Path $Py)) {
    Write-Host "[1/6] 创建 venv ..."
    $basePython = @(Find-BasePython)
    if ($basePython.Length -gt 1) {
        & $basePython[0] $basePython[1] -m venv .venv
    } else {
        & $basePython[0] -m venv .venv
    }
}

Write-Host "[2/6] 安装依赖 (lark-oapi + filelock) ..."
try {
    & $Pip install -q --upgrade pip | Out-Null
} catch {
    Write-Host "  ! pip 自升级失败，继续安装依赖"
}
try {
    & $Pip install -q -r requirements.txt -i $PypiMirror
} catch {
    & $Pip install -q -r requirements.txt
}
Write-Host "  ✓ 依赖就绪"

# ── 2. 交互配置 .env ─────────────────────────────────────
Write-Host "[3/6] 配置（直接回车用方括号里的默认值）"
Write-Host " · 飞书 app 凭据（开发者后台自建应用，需 bitable + im 权限）"
Ask-DotEnv "FEISHU_APP_ID" "飞书 APP_ID (cli_...)"
Ask-DotEnv "FEISHU_APP_SECRET" "飞书 APP_SECRET"

Write-Host " · 目标代码仓库（agent 在这里改代码，需 git 仓库且有 origin/main）"
Ask-DotEnv "PIPELINE_REPO_PATH" "目标仓库绝对路径，例如 C:\Users\you\project"
Ensure-Workspaces

Write-Host " · 各阶段默认 agent（cursor / claude / gemini / codex；可在飞书用「需求@xxx」按需覆盖）"
Ask-DotEnv "PIPELINE_ENGINE_CLARIFY" "澄清阶段 agent" "cursor"
Ask-DotEnv "PIPELINE_ENGINE_CODE" "开发阶段 agent" "cursor"
Ask-DotEnv "PIPELINE_ENGINE_REVIEW" "Review 阶段 agent" "cursor"
Write-Host " · 验收门命令（在 worktree 里跑，exit 0 通过；留空则不设门）"
Ask-DotEnv "PIPELINE_TEST_CMD" "测试/lint 命令，如 npm run lint"

# ── 3. 建飞书多维表格 ────────────────────────────────────
Write-Host "[4/6] 飞书多维表格"
$baseToken = Get-DotEnvValue "PIPELINE_BASE_TOKEN"
if (-not (Test-PlaceholderValue $baseToken)) {
    Write-Host "  已有 PIPELINE_BASE_TOKEN，跳过建表（要新建另一张表：$Py -B $Src\bootstrap.py --force）"
} else {
    & $Py -B (Join-Path $Src "bootstrap.py")
}

# ── 4. 自检 ──────────────────────────────────────────────
Write-Host "[5/6] 自检 ..."
try {
    & $Py -B (Join-Path $Src "doctor.py")
} catch {
    Write-Host "  ⚠ 有未通过项，按上面提示修复后可重跑 doctor.py"
}

# ── 5. 常驻任务（可选）───────────────────────────────────
Write-Host "[6/6] 常驻任务"
$svc = Read-Host "  创建 Windows 计划任务，登录时启动 listener + dispatcher? [y/N]"
if ($svc -match "^[Yy]") {
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
    New-Item -ItemType Directory -Force -Path "scripts\windows" | Out-Null

    $pathDirs = New-Object System.Collections.Generic.List[string]
    foreach ($bin in @("cursor-agent", "gemini", "claude", "codex", "gh", "git", "node", "npm")) {
        $cmd = Get-Command $bin -ErrorAction SilentlyContinue
        if ($cmd) {
            $cmdDir = Split-Path -Parent $cmd.Source
            if ($cmdDir -and -not $pathDirs.Contains($cmdDir)) {
                $pathDirs.Add($cmdDir)
            }
        }
    }
    $extraPath = [string]::Join(";", $pathDirs)

    function Write-Runner {
        param([string]$Name, [string]$ScriptName)
        $wrapper = Join-Path $Dir "scripts\windows\run-$Name.ps1"
        $log = Join-Path $Dir "logs\$Name.log"
        $safeDir = $Dir.Replace("'", "''")
        $safePy = $Py.Replace("'", "''")
        $safeScript = (Join-Path $Src $ScriptName).Replace("'", "''")
        $safeLog = $log.Replace("'", "''")
        $safePath = $extraPath.Replace("'", "''")
        @"
`$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath '$safeDir'
`$env:PATH = '$safePath;' + `$env:PATH
`$env:PYTHONDONTWRITEBYTECODE = '1'
& '$safePy' -B '$safeScript' *>> '$safeLog'
"@ | Set-Content $wrapper -Encoding UTF8
        return $wrapper
    }

    $listenerRunner = Write-Runner "listener" "listener.py"
    $dispatcherRunner = Write-Runner "dispatcher" "dispatcher.py"

    function Register-AgentPipelineTask {
        param([string]$TaskName, [string]$Runner)
        $action = New-ScheduledTaskAction `
            -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger `
            -Description "agent-pipeline $TaskName" `
            -Force | Out-Null
        try {
            Start-ScheduledTask -TaskName $TaskName
            Write-Host "  ✓ $TaskName 已创建并启动"
        } catch {
            Write-Host "  ✓ $TaskName 已创建；启动失败时可在任务计划程序里手动运行"
        }
    }

    Register-AgentPipelineTask "AgentPipelineListener" $listenerRunner
    Register-AgentPipelineTask "AgentPipelineDispatcher" $dispatcherRunner
}

Write-Host "═══════════════════════════════════════"
Write-Host " 完成。飞书私聊机器人发「需求@cursor：<一句话>」即可开跑。"
Write-Host " 手动跑：$Py -B $Src\dispatcher.py   /   $Py -B $Src\listener.py"
Write-Host "═══════════════════════════════════════"
