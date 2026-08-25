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
from langgraph.graph.state import CompiledStateGraph

from chuan.agents.base import AgentInstance, AgentResult


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
