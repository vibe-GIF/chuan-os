# 外来 agent 接入契约

本目录放**从 GitHub 等下载、精选后纳入班底**的外部 agent。
每个 agent 是一个自包含包（一个子文件夹），便于整体增删、审计、回滚。

## 目录形态

```
agents/
└── <agent_name>/              # 一个外部 agent = 一个子文件夹
    ├── agent.yaml             # 角色定义（格式同 personas/*.yaml，必填）
    ├── skills/                # 可选：该 agent 自带技能 YAML
    └── mcp_servers/           # 可选：该 agent 自带 MCP 服务（.py）
```

## agent.yaml 格式

与 `personas/*.yaml` 完全一致：

```yaml
name: example
display_name: 示例外来Agent
description: 从 GitHub 下载、纳入班底的示例
brain: cloud_general          # 复用大脑三档之一
external: true                # 标记为外来 agent
prompt: |                     # 可选：追加到该 worker 的系统提示词
  遵循本包的专属工作流程。
# 可选：命令型 agent。用户任务经 stdin 传入，stdout 会作为工具结果返回。
# command: ["python", "agent.py"]
# timeout_seconds: 60          # 1–600 秒，默认 60
```

## 纳入班底的步骤（必须显式，非自动）

1. 把下载的 agent 包放进 `agents/<agent_name>/`
2. 在 `config.yaml` 的 `external_agents.enabled` 列表里加 `<agent_name>`
3. 如需新工具，在 `config/mcp_servers.yaml` 登记其 `mcp_servers/` 下的服务
4. 幕僚长路由（`chief_of_staff.yaml`）识别到该角色后，即可 handoff

### 两种接入形态

- **prompt 型（默认）**：提供 `prompt`（或只提供普通角色字段）。它会作为带
  专属 system prompt 的 worker 出生。
- **command 型**：提供 `command: [executable, arg, ...]`。它本身会成为幕僚长可
  直接路由的 worker；任务通过 stdin 交给命令，stdout 就是该 worker 的回复。命令不
  经过 shell，并在运行前强制经过 `guard.py` 审核。

## Pi coding agent

本项目已将 `agents/pi/` 注册为幕僚长的直属 `pi` worker，不是普通工具。它通过
Docker 运行 Pi，宿主机只向容器挂载本项目工作目录；Pi 的文件与命令操作因此限制在
该目录和容器中。首次使用前：

```powershell
docker build -t chuan-pi-sandbox agents/pi
$env:OPENROUTER_API_KEY = "..."
$env:CHUAN_PI_MODEL = "openrouter/openai/gpt-4.1-mini"
```

运行时使用 `--no-approve` 忽略项目本地 Pi 资源，容器移除了 Linux capabilities、禁止
提权，并限制为 4 CPU / 4 GB / 256 进程。Pi 仍需要网络连接以调用模型提供商。

## 约束（与「固定可控班底」一致）

- **不自动发现**：不在 `enabled` 列表里的 agent 不会被注册为可用角色
- **过安全闸**：所有外部 agent 输出同样走 `guard.py`
- **最小权限**：其工具按最小权限注册，不默认全开
- **命名空间隔离**：外部 agent 在共享黑板的写入走 `shared/external/<agent_name>/`，不污染核心角色
- **可一键移除**：从 `enabled` 列表删名即停用；删文件夹即彻底移除

## 与公共智能体市场的区别

豆包智能体市场 2026-07 已全面下线。本机制刻意**不做**「扫描目录自动加载所有 agent」
那套——外部 agent 是精选手工接入、可审计、可回滚，不是开放式市场。
