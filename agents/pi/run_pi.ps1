# Pi 直接执行入口（无 Docker）
$ErrorActionPreference = "Continue"

# 自动安装（如缺失）
$cmd = Get-Command "pi" -ErrorAction SilentlyContinue
if (-not $cmd) {
    Write-Warning "pi 未安装，正在自动安装..."
    npm install -g @earendil-works/pi-coding-agent 2>&1 | Out-Null
    $cmd = Get-Command "pi" -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Output "[PI ERROR] 安装失败，请手动: npm install -g @earendil-works/pi-coding-agent"
        exit 1
    }
}

$task = [Console]::In.ReadToEnd()

$tmpFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($tmpFile, $task, [System.Text.Encoding]::UTF8)

    $workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    # 位置参数作为 prompt 传入（-p/--print 是非交互模式开关，不是 prompt 参数）
    # 注意：pi v0.84.2 在 Windows 上 -p 模式存在 prompt 传入 bug，待升级或改用 RPC 模式
    $output = & pi --provider zhipu --model glm-4-flash --print --no-session "$(Get-Content $tmpFile -Raw)" 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0 -and $output) {
        $output | Out-String | Write-Output
    } else {
        Write-Output "[PI ERROR exit=$exitCode] $output"
        exit $exitCode
    }
} finally {
    if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force }
}