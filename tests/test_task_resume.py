"""N35 断点续跑：子任务结果缓存（打断不丢工具）+ 角色 resume 复用。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from chuan.gateway.task_resume import RoleTaskResumeStore
from chuan.role import Department, SubTask


def _store(tmp_path: Path) -> RoleTaskResumeStore:
    return RoleTaskResumeStore(tmp_path)


# --------------------------------------------------------------------- #
# TaskResumeStore：plan + 结果持久化
# --------------------------------------------------------------------- #
def test_save_plan_then_resume_plan(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
    ])
    doc = store.resume_plan("sess1")
    assert doc is not None
    assert doc["task"] == "调研方案"
    assert len(doc["plan"]) == 2
    assert doc["plan"][0]["description"] == "查资料"
    assert doc["plan"][1]["depends_on"] == ["s1"]
    assert doc["results"] == {}


def test_save_result_updates_resume_plan(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
    ])
    store.save_result("sess1", "s1", success=True, content="查到资料A", agent="builtin")
    doc = store.resume_plan("sess1")
    assert doc["results"]["s1"]["success"] is True
    assert doc["results"]["s1"]["content"] == "查到资料A"
    assert "s2" not in doc["results"]  # 未完成子任务无结果


def test_no_plan_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.resume_plan("nope") is None


def test_list_resumable_counts_progress(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
        {"id": "s3", "description": "出报告", "agent": "auto", "depends_on": ["s2"]},
    ])
    store.save_result("sess1", "s1", success=True, content="A")
    items = store.list_resumable()
    assert len(items) == 1
    assert items[0]["session_id"] == "sess1"
    assert items[0]["total"] == 3 and items[0]["done"] == 1


def test_clear_removes_archive(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
    ])
    assert store.clear("sess1") is True
    assert store.resume_plan("sess1") is None
    assert store.clear("sess1") is False  # 幂等


def test_result_truncated_to_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "任务", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
    ])
    store.save_result("sess1", "s1", success=True, content="x" * 5000)
    doc = store.resume_plan("sess1")
    assert len(doc["results"]["s1"]["content"]) <= 4000


# --------------------------------------------------------------------- #
# Department：_rehydrate_plan + _run_subtask resume 复用
# --------------------------------------------------------------------- #
def test_rehydrate_plan_builds_subtasks() -> None:
    plan = Department._rehydrate_plan([
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
    ])
    assert [st.id for st in plan] == ["s1", "s2"]
    assert plan[1].depends_on == ["s1"]
    assert isinstance(plan[0], SubTask)


def test_rehydrate_plan_empty_raises() -> None:
    try:
        Department._rehydrate_plan([])
    except Exception:
        return
    raise AssertionError("空缓存 plan 应抛错")


class _RecorderAgent:
    """记录被调用的 agent（验证 resume 跳过 agent 调用）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, task: str, context=None):
        self.calls.append(task)
        return _Result(task)


class _Result:
    def __init__(self, content: str) -> None:
        self.content = content
        self.success = True
        self.agent_name = "fake"


class _RecorderPool:
    def __init__(self) -> None:
        self.agent = _RecorderAgent()
        self.calls: list[str] = []

    def get(self, name):
        return self.agent

    def list_resident(self):
        return []

    def get_model(self, name):
        return None

    def get_builtin_agent(self, name, checkpointer=None):
        self.calls.append(f"builtin:{name}")
        return self.agent


class _P:
    def __init__(self, name: str) -> None:
        self.name = name
        self.display_name = name
        self.description = "测试"


def _make_role(store: RoleTaskResumeStore) -> tuple[Department, _RecorderPool]:
    pool = _RecorderPool()
    role = Department(_P("研究"), pool, memory=None, resume_store=store)
    return role, pool


async def test_resume_skips_completed_subtask(tmp_path: Path) -> None:
    """已完成子任务复用缓存结果，未完成的才调 agent。"""
    store = _store(tmp_path)
    # 预置断点：s1 已完成
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
    ])
    store.save_result("sess1", "s1", success=True, content="已有资料", agent="builtin")

    role, pool = _make_role(store)

    # resume 路径：_should_plan 必须为 True 才会进规划分支；_plan 会失败 → plan=[]
    # 所以直接测 resume 的 _execute + _run_subtask 复用层
    plan = role._rehydrate_plan(store.resume_plan("sess1")["plan"])
    results = await role._execute(
        plan, "sess1", task="调研方案",
        resume_hits=dict(store.resume_plan("sess1")["results"]),
    )
    assert results["s1"].success is True
    assert "[续跑复用]" in results["s1"].content
    assert "已有资料" in results["s1"].content
    # s2 未完成 → 走 agent（s1 依赖已由 results 满足）
    assert results["s2"].success is True
    assert pool.agent.calls, "未完成的 s2 应调用 agent"


async def test_resume_runs_all_when_no_results(tmp_path: Path) -> None:
    """无缓存结果时 resume 等价于正常执行（全部调 agent）。"""
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
    ])
    role, pool = _make_role(store)
    plan = role._rehydrate_plan(store.resume_plan("sess1")["plan"])
    results = await role._execute(plan, "sess1", task="调研方案", resume_hits={})
    assert len(results) == 2
    assert pool.agent.calls, "无缓存结果时应全部执行"


async def test_resume_execute_saves_new_results(tmp_path: Path) -> None:
    """续跑时新完成的子任务结果继续写回缓存。"""
    store = _store(tmp_path)
    store.save_plan("sess1", "研究", "调研方案", [
        {"id": "s1", "description": "查资料", "agent": "auto", "depends_on": []},
        {"id": "s2", "description": "写总结", "agent": "auto", "depends_on": ["s1"]},
    ])
    role, _ = _make_role(store)
    plan = role._rehydrate_plan(store.resume_plan("sess1")["plan"])
    await role._execute(plan, "sess1", task="调研方案", resume_hits={})
    doc = store.resume_plan("sess1")
    assert "s1" in doc["results"] and "s2" in doc["results"]
    assert doc["results"]["s1"]["content"]
    assert doc["results"]["s2"]["content"]


# --------------------------------------------------------------------- #
# 主入口：run 帮助函数
# --------------------------------------------------------------------- #
def run(coro) -> None:
    asyncio.run(coro)
