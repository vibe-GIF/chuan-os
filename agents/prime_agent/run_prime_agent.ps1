# Prime Agent 直接执行入口（无 Docker 开销）
$ErrorActionPreference = "Continue"

# 自动安装（如缺失）
$cmd = Get-Command "prime-agent" -ErrorAction SilentlyContinue
if (-not $cmd) {
    Write-Warning "prime-agent 未安装，正在自动安装..."
    npm install -g prime-agent 2>&1 | Out-Null
    $cmd = Get-Command "prime-agent" -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Output "[PRIME AGENT ERROR] 安装失败，请手动: npm install -g prime-agent"
        exit 1
    }
}

$task = [Console]::In.ReadToEnd()

$tmpFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($tmpFile, $task, [System.Text.Encoding]::UTF8)

    $workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $output = & prime-agent --print --no-session "@$tmpFile" 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0 -and $output) {
        $output | Out-String | Write-Output
    } else {
        Write-Output "[PRIME AGENT ERROR exit=$exitCode] $output"
        exit $exitCode
    }
} finally {
    if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force }
}