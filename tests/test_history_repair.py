"""悬空 tool_calls 历史修复（INVALID_CHAT_HISTORY）测试。

上次会话在工具执行前中断 → 检查点留下无 ToolMessage 结果的 AIMessage
→ 重启重放被 LLM 提供方拒绝。BuiltinAgent._repair_history 负责补占位结果。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chuan.agents.builtin import BuiltinAgent, dangling_tool_call_ids


def test_dangling_ids_empty_history() -> None:
    assert dangling_tool_call_ids([]) == []


def test_dangling_ids_no_tool_calls() -> None:
    msgs = [HumanMessage("你好"), AIMessage("在的")]
    assert dangling_tool_call_ids(msgs) == []


def test_dangling_ids_detects_unanswered_call() -> None:
    """AI 请求了工具但没有 ToolMessage 结果 → 悬空。"""
    ai = AIMessage(
        "",
        tool_calls=[{"name": "read_role_memory", "args": {"__arg1": "MEMORY.md"}, "id": "call_1"}],
    )
    assert dangling_tool_call_ids([HumanMessage("看记忆"), ai]) == ["call_1"]


def test_dangling_ids_answered_call_not_dangling() -> None:
    ai = AIMessage(
        "",
        tool_calls=[{"name": "read_role_memory", "args": {}, "id": "call_1"}],
    )
    tm = ToolMessage(content="（空）", tool_call_id="call_1")
    assert dangling_tool_call_ids([HumanMessage("看记忆"), ai, tm]) == []


def test_dangling_ids_multiple_and_partial() -> None:
    ai1 = AIMessage(
        "",
        tool_calls=[
            {"name": "tool_a", "args": {}, "id": "call_1"},
            {"name": "tool_b", "args": {}, "id": "call_2"},
        ],
    )
    tm = ToolMessage(content="ok", tool_call_id="call_1")
    ai2 = AIMessage(
        "",
        tool_calls=[{"name": "tool_c", "args": {}, "id": "call_3"}],
    )
    missing = dangling_tool_call_ids([ai1, tm, ai2])
    assert set(missing) == {"call_2", "call_3"}


class _FakeState:
    def __init__(
        self, values: dict[str, Any],
        checkpoint_id: str = "cp-latest", checkpoint_ns: str = "",
    ) -> None:
        self.values = values
        self.config: dict[str, Any] = {
            "configurable": {
                "thread_id": "t",
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }


class _FakeGraph:
    """最小可用的 CompiledStateGraph 替身：记录 aupdate_state 收到的补丁。"""

    def __init__(
        self, messages: list[Any],
        checkpoint_id: str = "cp-latest", checkpoint_ns: str = "",
    ) -> None:
        self._messages = messages
        self.updates: list[dict[str, Any]] = []
        self.checkpoint_id = checkpoint_id
        self.checkpoint_ns = checkpoint_ns

    async def aget_state(self, config: dict[str, Any]) -> _FakeState:
        return _FakeState(
            {"messages": self._messages},
            checkpoint_id=self.checkpoint_id, checkpoint_ns=self.checkpoint_ns,
        )

    async def aupdate_state(self, config: dict[str, Any], values: dict[str, Any]) -> None:
        self.updates.append(values)

    async def ainvoke(self, inputs: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"messages": [AIMessage("ok")]}


@pytest.mark.asyncio
async def test_repair_history_patches_dangling_calls() -> None:
    ai = AIMessage(
        "",
        tool_calls=[{"name": "read_role_memory", "args": {}, "id": "call_9"}],
    )
    graph = _FakeGraph([HumanMessage("看记忆"), ai])
    agent = BuiltinAgent(graph)  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": "t"}}
    await agent._repair_history(config)
    assert len(graph.updates) == 1
    patches = graph.updates[0]["messages"]
    assert len(patches) == 1
    assert isinstance(patches[0], ToolMessage)
    assert patches[0].tool_call_id == "call_9"
    assert "中断" in patches[0].content


@pytest.mark.asyncio
async def test_repair_history_clean_history_no_update() -> None:
    graph = _FakeGraph([HumanMessage("你好"), AIMessage("在的")])
    agent = BuiltinAgent(graph)  # type: ignore[arg-type]
    await agent._repair_history({"configurable": {"thread_id": "t"}})
    assert graph.updates == []


@pytest.mark.asyncio
async def test_repair_history_failure_is_silent() -> None:
    """aget_state 抛错（如无 checkpointer）时静默跳过，不阻断 run。"""

    class _Boom:
        async def aget_state(self, config: dict[str, Any]) -> Any:
            raise RuntimeError("no checkpointer")

    agent = BuiltinAgent(_Boom())  # type: ignore[arg-type]
    await agent._repair_history({"configurable": {"thread_id": "t"}})  # 不应抛错


@pytest.mark.asyncio
async def test_run_calls_repair_before_invoke() -> None:
    """run() 应先修复历史再 ainvoke（次序保证）。"""
    order: list[str] = []

    class _Ordered(_FakeGraph):
        async def aget_state(self, config: dict[str, Any]) -> _FakeState:
            order.append("get_state")
            return _FakeState({"messages": [AIMessage("hi")]})

        async def ainvoke(self, inputs: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
            order.append("invoke")
            return {"messages": [AIMessage("ok")]}

    agent = BuiltinAgent(_Ordered([]))  # type: ignore[arg-type]
    result = await agent.run("任务", {"thread_id": "t"})
    assert order == ["get_state", "invoke"]
    assert result.content == "ok"


# ── 删 checkpoint 策略（2026-08-25 改进）──────────────────────


class _FakeConn:
    """模拟 aiosqlite.Connection：记录 execute/commit。"""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.committed = 0

    async def execute(self, sql: str, params: tuple = ()) -> Any:
        self.executed.append((sql, params))
        class _Cur:
            rowcount = 1
        return _Cur()

    async def commit(self) -> None:
        self.committed += 1


class _FakeGraphWithCheckpointer(_FakeGraph):
    """带 checkpointer.conn 的 FakeGraph，走删 checkpoint 路径。"""

    def __init__(self, messages: list[Any], checkpoint_id: str = "cp-latest") -> None:
        super().__init__(messages, checkpoint_id=checkpoint_id)
        self.conn = _FakeConn()
        self.checkpointer = type("CP", (), {"conn": self.conn})()


@pytest.mark.asyncio
async def test_repair_history_deletes_dirty_checkpoint() -> None:
    """有 conn 时，首选删最新 checkpoint 而非追加 ToolMessage。"""
    ai = AIMessage(
        "",
        tool_calls=[{"name": "read_role_memory", "args": {}, "id": "call_x"}],
    )
    graph = _FakeGraphWithCheckpointer([HumanMessage("看记忆"), ai], checkpoint_id="cp-dirty")
    agent = BuiltinAgent(graph)  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": "t_del"}}
    await agent._repair_history(config)
    # 不应该走追加路径
    assert graph.updates == []
    # 应该执行了 DELETE SQL（checkpoints + writes），且用精确 checkpoint_id（非 ORDER BY）
    dels = [
        (sql, p) for sql, p in graph.conn.executed if sql.startswith("DELETE FROM")
    ]
    assert len(dels) == 2
    for sql, p in dels:
        assert "ORDER BY" not in sql
        assert p == ("t_del", "", "cp-dirty")  # thread_id, checkpoint_ns, checkpoint_id
    assert graph.conn.committed >= 1


@pytest.mark.asyncio
async def test_repair_history_no_checkpoint_id_falls_back_to_append() -> None:
    """aget_state 没返回 checkpoint_id（边界）→ 不删，走追加占位 ToolMessage 兜底。"""
    ai = AIMessage(
        "",
        tool_calls=[{"name": "read_role_memory", "args": {}, "id": "call_w"}],
    )
    graph = _FakeGraphWithCheckpointer([HumanMessage("看记忆"), ai], checkpoint_id="")
    agent = BuiltinAgent(graph)  # type: ignore[arg-type]
    await agent._repair_history({"configurable": {"thread_id": "t_w"}})
    # 拿不到精确 id → 不执行 DELETE，走追加
    assert not any(
        sql.startswith("DELETE FROM") for sql, _ in graph.conn.executed
    )
    assert len(graph.updates) == 1
    assert isinstance(graph.updates[0]["messages"][0], ToolMessage)


@pytest.mark.asyncio
async def test_repair_history_fallback_to_append_without_conn() -> None:
    """无 conn（如 InMemorySaver）时，兜底追加占位 ToolMessage。"""
    ai = AIMessage(
        "",
        tool_calls=[{"name": "read_role_memory", "args": {}, "id": "call_y"}],
    )
    graph = _FakeGraph([HumanMessage("看记忆"), ai])  # 无 checkpointer.conn
    agent = BuiltinAgent(graph)  # type: ignore[arg-type]
    await agent._repair_history({"configurable": {"thread_id": "t_fallback"}})
    assert len(graph.updates) == 1
    assert isinstance(graph.updates[0]["messages"][0], ToolMessage)
