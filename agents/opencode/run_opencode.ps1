# OpenCode 直接执行入口（JSON 模式，自动提取回复文本）
$ErrorActionPreference = "Continue"

$cmd = Get-Command "opencode" -ErrorAction SilentlyContinue
if (-not $cmd) {
    Write-Output "[OPENCODE ERROR] opencode 未安装。"
    exit 1
}

$task = [Console]::In.ReadToEnd()

# 使用 JSON 格式输出，从 JSON 事件中提取文本回复
$jsonOutput = & opencode run --format json "$task" 2>&1
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Output "[OPENCODE ERROR exit=$exitCode] $jsonOutput"
    exit $exitCode
}

# 从 JSON 行中提取 text 类型的内容
$replies = @()
foreach ($line in $jsonOutput) {
    try {
        $parsed = $line | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($parsed -and $parsed.type -eq "text" -and $parsed.part.text) {
            $replies += $parsed.part.text
        }
    } catch {
        # 跳过非 JSON 行
    }
}

if ($replies.Count -gt 0) {
    Write-Output ($replies -join "`n")
} else {
    Write-Output "[OPENCODE COMPLETED]"
}