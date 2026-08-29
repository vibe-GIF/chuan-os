"""Agent 实现层 —— 岗位调度的干活实例。

三种 agent 形态:
- builtin:  内置 LangGraph ReAct agent（轻量、免费、简单任务）
- command:  通过 stdin/stdout 子进程调用外部 agent（pi / OpenCode / Claude Code）
- mcp:      通过 MCP 协议调用（预留）

所有 agent 统一继承 AgentInstance 基类，暴露 async run(task, context) 接口。
岗位（Department）从 agent_pool 取 agent 调用，不关心具体实现。
"""
