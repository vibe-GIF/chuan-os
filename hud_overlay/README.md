# assistant_overlay — 贾维斯全息 HUD 悬浮层

川流 chuan-os 的 Flutter 前端：透明悬浮窗口上渲染「贾维斯全息人体模型」环形动画、序列帧人物、AI/用户双终端与系统状态面板。由后端经 TCP（默认 `127.0.0.1:17889`）驱动，对话实时上屏。

## 与后端的关系

```
Python 后端 (chuan.channels.hud.HudChannel)
    │  TCP 17889，按行发命令
    ▼
assistant_overlay.exe（本 Flutter 应用，JarvisTCPServer 监听）
    ▼
   环形动画 + 终端文本 + 系统状态 + SUPERVISOR 面板
```

- 后端未启动 HUD 时**静默降级**，不阻断主流程（ADR-007 薄层）。
- 后端侧说明见 [chuan/channels/hud.py](../chuan/channels/hud.py) 与 [docs/guide/DEVELOPMENT.md](../docs/guide/DEVELOPMENT.md)。

## TCP 协议（文本行，`\n` 分隔）

| 命令 | 作用 |
|---|---|
| `wake` | 触发唤醒动画 |
| `hide` | 隐藏特效 |
| `agent:<name>` | 切换角色特效（jarvis / lin-meimei / xiao-nu） |
| `effect:<success\|error\|speaking\|processing>` | 状态色切换 |
| `user:<text>` | 用户终端区文本 |
| `ai:<text>` | AI 终端区文本 |
| `monitor:{json}` | 监督者快照（SUPERVISOR 面板：ACTIVE/DEAD/RD + 最近死胡同） |

## 构建

```powershell
# 一键构建（含 Windows 符号链接修复）：
scripts\package.bat

# 或手动：
flutter pub get
flutter build windows --release
# 产物：build\windows\x64\runner\Release\assistant_overlay.exe
```

- Flutter SDK 位于项目根 `.flutter_sdk\flutter`（3.47.1，本地路径，不在 PATH）。
- `flutter clean` 后需先跑 `scripts\fix_plugin_symlinks.ps1`（package.bat 已自动集成），否则 Windows 符号链接权限问题导致构建失败。

## 运行

```powershell
# 前台（窗口鼠标穿透，无法点关；停用杀进程）：
build\windows\x64\runner\Release\assistant_overlay.exe

# 停止：
Get-Process assistant_overlay | Stop-Process -Force
```

启动后可 `Test-NetConnection 127.0.0.1 -Port 17889` 确认监听。

## 资源素材

- 序列帧位于 `assets/jarvis/`（人体模型，~250 帧）与 `assets/ironman/`（MK7 半身像，~180 帧），由仓库 Git LFS 统一管理。
- 若目录里是文本指针而非真实 PNG：执行 `git lfs pull`，或从 ModelScope 直连下载（`rubintry/jarvis` 数据集 `/resolve/master/`），解压后重建。
- HUD 常驻内存较大（~3.9GB），系 430 帧 PNG 预加载的固有设计。

## 代码结构

```
lib/
├── main.dart               # 应用入口（透明窗口 + AgentOverlay）
├── agent_overlay.dart      # 多 agent 调度 + TCP 命令分发
├── jarvis_overlay.dart     # Jarvis 主视觉：环形动画 + 序列帧 + 终端/状态面板
├── jarvis_rings_windows.dart
├── hud_terminal_shell.dart # 终端壳组件（标题 + 亮角外框 + 内容布局）
├── linmeimei_overlay.dart  # 林妹妹角色
├── tcp_server.dart         # JarvisTCPServer（监听 + 按行回调）
└── overlay/                # 未接线的重构副本（活跃路径为根目录文件）
```
