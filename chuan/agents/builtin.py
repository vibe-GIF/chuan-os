"""内置 ReAct agent —— 轻量、免费、简单任务首选。

基于 LangGraph create_react_agent，用 local 或 cloud_general 脑。
岗位可以动态 spawn 多个实例，各配各的 system prompt 和工具子集。

阶段1：构造函数接收一个已由 persona_loader.birth() 创建好的
CompiledStateGraph，不再自己 create_react_agent。
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from chuan.agents.base import AgentInstance, AgentResult


def dangling_tool_call_ids(messages: list[Any]) -> list[str]:
    """找出悬空 tool_call（AIMessage 请求了工具但没有对应 ToolMessage 结果）的 id。

    上次会话在工具执行前中断时，检查点里会留下这种悬空调用；重启后
    重放历史会被 LLM 提供方校验拒绝（INVALID_CHAT_HISTORY）。
    """
    pending: dict[str, str] = {}  # call_id -> 工具名
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                cid = str(call.get("id") or "")
                if cid:
                    pending[cid] = str(call.get("name") or "tool")
        elif isinstance(msg, ToolMessage):
            pending.pop(str(msg.tool_call_id), None)
    return list(pending.keys())


class _ToolProgressHandler(BaseCallbackHandler):
    """把 LangGraph 工具调用（含 recall_memory / remember_memory / MCP）转成进度事件。

    role 通过 context["__on_progress__"] 注入回调；本 handler 在工具开始时
    上报 ``tool_call``，结束时上报 ``tool_done``（含耗时），供 TUI 画路由树。
    """

    def __init__(self, emit: Any) -> None:
        self._emit = emit
        self._start_at: dict[str, float] = {}
        self._names: dict[str, str] = {}

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        run_id = str(kwargs.get("run_id") or "")
        name = (serialized or {}).get("name", "tool")
        self._names[run_id] = name
        self._start_at[run_id] = time.monotonic()
        self._emit({
            "event": "tool_call", "tool": name, "input": str(input_str),
        })

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        start = self._start_at.pop(run_id, None)
        name = self._names.pop(run_id, "tool")
        elapsed = (time.monotonic() - start) if start is not None else 0.0
        self._emit({
            "event": "tool_done", "tool": name, "output": str(output),
            "elapsed": elapsed,
        })

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        start = self._start_at.pop(run_id, None)
        name = self._names.pop(run_id, "tool")
        elapsed = (time.monotonic() - start) if start is not None else 0.0
        self._emit({
            "event": "tool_done", "tool": name, "error": str(error),
            "elapsed": elapsed,
        })


class BuiltinAgent(AgentInstance):
    """内置 LangGraph ReAct agent。

    阶段1：封装 persona_loader.birth() 返回的 CompiledStateGraph，
    提供统一的 async run(task, context) 接口。
    """

    name = "builtin"
    display_name = "内置 ReAct"
    description = "轻量内置 agent，简单任务首选"

    def __init__(self, graph: CompiledStateGraph, name: str = "builtin") -> None:
        """接收已创建好的 CompiledStateGraph。

        Args:
            graph: 由 persona_loader.birth() 创建的 ReAct agent 图
            name: agent 名称（通常等于 persona 名）
        """
        self.name = name
        self._graph: CompiledStateGraph = graph

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """调用内置 ReAct agent 执行任务。

        Args:
            task: 用户任务描述
            context: 上下文；可含 "thread_id" 键用于 LangGraph checkpointer 会话隔离

        Returns:
            AgentResult 封装的执行结果
        """
        thread_id = "default"
        on_progress = None
        if context:
            thread_id = context.get("thread_id", thread_id)
            on_progress = context.get("__on_progress__")
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        if on_progress is not None:
            # LangChain 回调在图上每个工具调用前后触发，供 TUI 画工具/记忆召回行
            config["callbacks"] = [_ToolProgressHandler(on_progress)]
        # 先修复检查点里的悬空 tool_calls（上次会话中断残留），否则重放历史
        # 会被 LLM 提供方校验拒绝（INVALID_CHAT_HISTORY）
        await self._repair_history(config)
        # 用 ainvoke 而非 invoke：MCP 工具是异步的，同步 invoke 会报
        # "StructuredTool does not support sync invocation"
        result = await self._graph.ainvoke(
            {"messages": [("user", task)]},
            config=config,
        )
        messages = result.get("messages", [])
        content = ""
        if messages:
            last = messages[-1]
            content = getattr(last, "content", str(last))
        return AgentResult(content=content, agent_name=self.name)

    async def _repair_history(self, config: dict[str, Any]) -> None:
        """修复检查点里悬空的 tool_calls，避免 LLM 拒绝重放。

        上次会话在工具执行前中断时，检查点会留下"请求了工具但没有
        结果"的 AIMessage；重启后直接 ainvoke 会被 LLM 提供方校验拒绝
        （INVALID_CHAT_HISTORY / insufficient tool messages）。

        策略（2026-08-25 改进）：
        - **首选**：删掉包含悬空 tool_call 的最新 checkpoint，让 LangGraph
          从上一个干净状态恢复。比追加 ToolMessage 更可靠——因为
          ``aupdate_state`` 只能追加到末尾，ToolMessage 不在 AIMessage
          紧后面，LLM 仍会拒绝。
        - **兜底**：checkpointer 无 SQL 接口（如 InMemorySaver）时，
          追加占位 ToolMessage（旧逻辑，位置可能不正确但尽力而为）。

        任何失败都静默跳过（不阻断正常调用）。
        """
        try:
            state = await self._graph.aget_state(config)
            msgs = (state.values or {}).get("messages") or [] if state else []
            if not msgs:
                return
            missing = dangling_tool_call_ids(list(msgs))
            if not missing:
                return
            # 首选：删掉含悬空 tool_call 的最新 checkpoint（用 aget_state 返回的
            # 精确 checkpoint_id，避免 checkpoint_id 是 UUID 时按字符串排序删错）
            cpid, cns = "", ""
            if state is not None and getattr(state, "config", None):
                _conf = state.config.get("configurable") or {}
                cpid = str(_conf.get("checkpoint_id") or "")
                cns = str(_conf.get("checkpoint_ns") or "")
            if await self._delete_latest_checkpoint(config, cpid, cns):
                return
            # 兜底：追加占位 ToolMessage（非 SQLite checkpointer 场景）
            patches = [
                ToolMessage(
                    content="（上次会话中断，该工具未执行完成，请基于已有信息继续）",
                    tool_call_id=cid,
                )
                for cid in missing
            ]
            await self._aupdate_messages(config, patches)
        except Exception:  # noqa: BLE001 - 修复失败不阻断主流程
            return

    async def _delete_latest_checkpoint(
        self, config: dict[str, Any], checkpoint_id: str = "", checkpoint_ns: str = ""
    ) -> bool:
        """删指定 checkpoint（含悬空 tool_call），让 LangGraph 从上一状态恢复。

        用 aget_state 返回的精确 checkpoint_id（+ checkpoint_ns），而非按
        checkpoint_id 字符串排序——checkpoint_id 是 UUID，字符串序 ≠ 时间序，
        排序会删错。拿不到精确 id 时返回 False，走追加占位 ToolMessage 兜底。
        """
        try:
            if not checkpoint_id:
                return False
            checkpointer = getattr(self._graph, "checkpointer", None)
            conn = getattr(checkpointer, "conn", None)
            if conn is None:
                return False
            thread_id = config["configurable"]["thread_id"]
            await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, checkpoint_ns, checkpoint_id),
            )
            await conn.execute(
                "DELETE FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, checkpoint_ns, checkpoint_id),
            )
            await conn.commit()
            return True
        except Exception:  # noqa: BLE001 - 非 SQLite checkpointer 或无 conn
            return False

    def _messages_node_name(self) -> str:
        """探测一个写入 messages 的节点名，供 aupdate_state 消歧。

        LangGraph 1.2+ 里，多个节点写同一 channel 时 update_state 必须
        指定 as_node，否则抛 InvalidUpdateError（Ambiguous update）。
        create_react_agent 有 agent/tools 两个 messages 写入者。
        """
        try:
            nodes = list(self._graph.get_graph().nodes.keys())
        except Exception:  # noqa: BLE001
            return "agent"
        for name in ("agent", "tools", "model", "chat"):
            if name in nodes:
                return name
        return next(
            (n for n in nodes if n not in ("__start__", "__end__")), "agent"
        )

    async def _aupdate_messages(
        self, config: dict[str, Any], patches: list[ToolMessage]
    ) -> None:
        """补写 messages：先裸调（老版 LangGraph/单节点图），歧义则带 as_node 重试。"""
        try:
            await self._graph.aupdate_state(config, {"messages": patches})
            return
        except Exception:  # noqa: BLE001 - 捕获 InvalidUpdateError，带 as_node 重试
            await self._graph.aupdate_state(
                config, {"messages": patches}, as_node=self._messages_node_name()
            )
