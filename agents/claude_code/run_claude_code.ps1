# Claude Code 直接执行入口（无 Docker）
$ErrorActionPreference = "Continue"

# 自动安装（如缺失）
$cmd = Get-Command "claude" -ErrorAction SilentlyContinue
if (-not $cmd) {
    Write-Warning "claude 未安装，正在自动安装..."
    npm install -g @anthropic-ai/claude-code 2>&1 | Out-Null
    $cmd = Get-Command "claude" -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Output "[CLAUDE CODE ERROR] 安装失败，请手动: npm install -g @anthropic-ai/claude-code"
        exit 1
    }
}

$task = [Console]::In.ReadToEnd()

$tmpFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($tmpFile, $task, [System.Text.Encoding]::UTF8)

    $workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $output = & claude --print "$(Get-Content $tmpFile -Raw)" 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0 -and $output) {
        $output | Out-String | Write-Output
    } else {
        Write-Output "[CLAUDE CODE ERROR exit=$exitCode] $output"
        exit $exitCode
    }
} finally {
    if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force }
}