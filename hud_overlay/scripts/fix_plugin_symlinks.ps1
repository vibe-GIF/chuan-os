# fix_plugin_symlinks.ps1
#
# 修复 Flutter Windows 构建时的插件符号链接问题。
#
# 背景：
#   Flutter 通过 dart:io 的 Link.createSync 在
#   {platform}/flutter/ephemeral/.plugin_symlinks 下为每个插件创建符号链接。
#   但在 Windows 上它会在「成功创建链接」之后仍抛出 errno=2（文件未找到）并中断，
#   导致每次构建只推进一个插件；而 PowerShell 的 New-Item -ItemType SymbolicLink
#   又要求管理员权限。
#
# 方案：
#   改用不需要管理员权限的「目录联接（Junction）」，对 Flutter/CMake 的插件解析
#   同样有效。这些链接位于 ephemeral 目录（已被 .gitignore 忽略），flutter clean
#   之后会丢失，因此每次 clean 后、构建前需要重跑本脚本。
#
# 用法（在项目已执行 flutter pub get 之后）：
#   powershell -ExecutionPolicy Bypass -File .\scripts\fix_plugin_symlinks.ps1

$ErrorActionPreference = 'Stop'

# 本脚本位于 hud_overlay\scripts\，项目目录为上一级
$hudOverlayDir = Split-Path -Parent $PSScriptRoot
$depsFile = Join-Path $hudOverlayDir '.flutter-plugins-dependencies'

if (-not (Test-Path $depsFile)) {
    Write-Host "[错误] 未找到 $depsFile，请先在 hud_overlay 下执行 flutter pub get" -ForegroundColor Red
    exit 1
}

$deps = Get-Content $depsFile -Raw | ConvertFrom-Json

# Flutter 仅为 Windows / Linux 平台使用 .plugin_symlinks 目录（macOS 走 CocoaPods）
$platforms = @('windows', 'linux')
$created = 0
$skipped = 0
$failed = 0

foreach ($platform in $platforms) {
    $plugins = $deps.plugins.$platform
    if (-not $plugins) { continue }

    $symlinkDir = Join-Path $hudOverlayDir "$platform\flutter\ephemeral\.plugin_symlinks"
    New-Item -ItemType Directory -Path $symlinkDir -Force | Out-Null

    foreach ($plugin in $plugins) {
        $name = $plugin.name
        # JSON 中的 path 末尾带有反斜杠，去掉后再用作 Junction 目标
        $source = $plugin.path -replace '[\\/]+$', ''

        if (-not (Test-Path $source)) {
            Write-Host "[警告] 源目录不存在，跳过：$name -> $source" -ForegroundColor Yellow
            $skipped++
            continue
        }

        $linkPath = Join-Path $symlinkDir $name
        if (Test-Path $linkPath) {
            Write-Host "[跳过] 已存在：$platform/$name" -ForegroundColor DarkGray
            $skipped++
            continue
        }

        try {
            New-Item -ItemType Junction -Path $linkPath -Target $source -ErrorAction Stop | Out-Null
            Write-Host "[成功] $platform/$name -> $source" -ForegroundColor Green
            $created++
        }
        catch {
            Write-Host "[失败] $platform/$name : $($_.Exception.Message)" -ForegroundColor Red
            $failed++
        }
    }
}

Write-Host ""
Write-Host "完成：新建 $created，跳过 $skipped，失败 $failed"
if ($failed -gt 0) { exit 1 }