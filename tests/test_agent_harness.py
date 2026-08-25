"""Agent Harness（fire-and-forget 后台委派）测试。

用 sys.executable 跑一个 echo 子进程模拟外部 command agent，
不依赖任何外部安装（pi / claude_code / opencode）。
"""

from __future__ import annotations

import sys
import time

from chuan.agents.command import CommandAgent
from chuan.agent_pool import AgentPool
from chuan.gateway.agent_harness import AgentHarness


class _FakeLoader:
    """最小 persona_loader 替身（AgentPool 只用 external_agents 属性）。"""

    external_agents = None


def _echo_agent(name: str = "fake_agent", *, sleep: float = 0.0) -> CommandAgent:
    """返回一个读 stdin 并回显的 command agent；可选先 sleep 模拟耗时。"""
    code = (
        "import sys, time\n"
        f"time.sleep({sleep})\n"
        "data = sys.stdin.read().strip()\n"
        "print('ECHO:' + data)\n"
    )
    return CommandAgent(
        name=name,
        command=[sys.executable, "-c", code],
        display_name=f"Fake {name}",
        timeout=30,
    )


def _make_harness() -> AgentHarness:
    pool = AgentPool(_FakeLoader())
    return AgentHarness(pool)


def _wait_done(
    harness: AgentHarness, task_id: str, timeout: float = 15.0
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = harness.get(task_id)
        if info and info["status"] in ("done", "failed"):
            return info
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} 未在 {timeout}s 内完成: {harness.get(task_id)}")


# ---------------------------------------------------------------------- #
# 基础委派
# ---------------------------------------------------------------------- #
def test_submit_runs_and_returns_result() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent())  # noqa: SLF001 - 测试直连内部池

    task_id = harness.submit("fake_agent", "给我写个 hello")

    # 立即返回 task_id（fire-and-forget 核心），且初始为 running
    assert task_id.startswith("delegate-")
    running = harness.get(task_id)
    assert running is not None and running["status"] == "running"

    info = _wait_done(harness, task_id)
    assert info["status"] == "done"
    assert info["success"] is True
    assert info["result"] == "ECHO:给我写个 hello"
    assert info["finished_at"] is not None


def test_unknown_agent_fails_gracefully() -> None:
    harness = _make_harness()

    task_id = harness.submit("not_exist", "随便干点")
    info = _wait_done(harness, task_id)
    assert info["status"] == "failed"
    assert info["success"] is False
    assert "未知 agent" in info["result"]


def test_snapshot_filter() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent())

    t1 = harness.submit("fake_agent", "任务一")
    _wait_done(harness, t1)

    assert harness.snapshot(status="done")
    assert len(harness.snapshot()) >= 1


# ---------------------------------------------------------------------- #
# fire-and-forget 语义：派发即返，不阻塞调用线程
# ---------------------------------------------------------------------- #
def test_submit_returns_immediately() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent(sleep=1.0))

    start = time.monotonic()
    task_id = harness.submit("fake_agent", "慢任务")
    elapsed = time.monotonic() - start

    # submit 不应等待任务跑完（任务要 sleep 1s，submit 必须远小于此）
    assert elapsed < 0.5, f"submit 阻塞了 {elapsed:.2f}s，fire-and-forget 失效"
    assert harness.get(task_id)["status"] == "running"


def test_concurrent_submits_all_complete() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent())

    ids = [harness.submit("fake_agent", f"并行任务{i}") for i in range(3)]
    results = [_wait_done(harness, tid) for tid in ids]
    assert all(r["status"] == "done" for r in results)
    assert {r["result"] for r in results} == {
        "ECHO:并行任务0", "ECHO:并行任务1", "ECHO:并行任务2",
    }


# ---------------------------------------------------------------------- #
# 完成回调
# ---------------------------------------------------------------------- #
def test_global_on_done_callback() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent())
    received: list[dict] = []
    harness.on_done(lambda info: received.append(info))

    task_id = harness.submit("fake_agent", "回调任务")
    _wait_done(harness, task_id)

    assert len(received) == 1
    assert received[0]["task_id"] == task_id
    assert received[0]["status"] == "done"


def test_per_task_on_done_callback() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent())
    received: list[dict] = []
    global_hits: list[str] = []

    harness.on_done(lambda info: global_hits.append(info["task_id"]))
    task_id = harness.submit(
        "fake_agent", "专属回调", on_done=lambda info: received.append(info)
    )
    _wait_done(harness, task_id)

    assert len(received) == 1  # 每任务回调触发
    assert received[0]["success"] is True
    assert task_id in global_hits  # 全局回调也触发


def test_callback_error_does_not_break() -> None:
    harness = _make_harness()
    harness._pool.register(_echo_agent())

    def bad_cb(info: dict) -> None:
        raise RuntimeError("回调炸了")

    harness.on_done(bad_cb)
    task_id = harness.submit("fake_agent", "坏回调")
    info = _wait_done(harness, task_id)
    assert info["status"] == "done"  # 回调异常被吞掉，任务状态不受影响


# ---------------------------------------------------------------------- #
# 任务状态机：pending/ready/running/done + 依赖 DAG
# ---------------------------------------------------------------------- #
def test_dependency_task_waits_then_runs() -> None:
    """B 依赖 A：B 初始 pending，A 完成后 B 自动推进并跑完。"""
    harness = _make_harness()
    harness._pool.register(_echo_agent())

    a = harness.submit("fake_agent", "前置任务")
    # B 刚提交时 A 还在跑 → B 必须 pending，不能提前执行
    b = harness.submit("fake_agent", "依赖任务", depends_on=[a])
    assert harness.get(b)["status"] == "pending"

    _wait_done(harness, a)
    info_b = _wait_done(harness, b)
    assert info_b["status"] == "done"
    assert info_b["result"] == "ECHO:依赖任务"
    # 执行顺序有保障：A 先于 B 结束
    assert harness.get(a)["finished_at"] <= info_b["finished_at"]


def test_dependency_chain() -> None:
    """A → B → C 链式依赖，逐个自动推进。"""
    harness = _make_harness()
    harness._pool.register(_echo_agent())

    a = harness.submit("fake_agent", "任务A")
    b = harness.submit("fake_agent", "任务B", depends_on=[a])
    c = harness.submit("fake_agent", "任务C", depends_on=[b])
    assert harness.get(b)["status"] == "pending"
    assert harness.get(c)["status"] == "pending"

    for tid in (a, b, c):
        info = _wait_done(harness, tid)
        assert info["status"] == "done"
    # 链式顺序：A → B → C 依次结束
    assert (harness.get(a)["finished_at"]
            <= harness.get(b)["finished_at"]
            <= harness.get(c)["finished_at"])


def test_submit_after_dep_done_runs_immediately() -> None:
    """依赖已结束后再提交 → 不经过 pending，直接 ready → running。"""
    harness = _make_harness()
    harness._pool.register(_echo_agent())

    a = harness.submit("fake_agent", "先完成")
    _wait_done(harness, a)

    b = harness.submit("fake_agent", "后提交", depends_on=[a])
    assert harness.get(b)["status"] == "running"  # 直接开跑，未停 pending
    info = _wait_done(harness, b)
    assert info["status"] == "done"


def test_dependency_on_failed_dep_still_runs() -> None:
    """依赖失败不阻断下游：failed 也是终态，下游照常运行。"""
    harness = _make_harness()
    harness._pool.register(_echo_agent())
    a = harness.submit("not_exist", "会失败的前置")
    _wait_done(harness, a)
    assert harness.get(a)["status"] == "failed"

    b = harness.submit("fake_agent", "下游任务", depends_on=[a])
    assert harness.get(b)["status"] == "running"
    info = _wait_done(harness, b)
    assert info["status"] == "done"


def test_depends_on_unknown_raises() -> None:
    """depends_on 引用不存在的任务 → ValueError（防环/防悬空引用）。"""
    harness = _make_harness()
    try:
        harness.submit("fake_agent", "x", depends_on=["delegate-999"])
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass


def test_claim_once_no_duplicate_execution() -> None:
    """认领原子性：_schedule 只把 ready 置 running 一次，重复调用不再执行。"""
    harness = _make_harness()
    harness._pool.register(_echo_agent(sleep=0.3))
    received: list[str] = []
    harness.on_done(lambda info: received.append(info["task_id"]))

    tid = harness.submit("fake_agent", "只跑一次")
    # 手工再调度两次：应被认领保护拦下（status 已不是 ready）
    harness._schedule(tid)
    harness._schedule(tid)
    info = _wait_done(harness, tid)
    assert info["status"] == "done"
    assert info["claimed_by"] == "harness"
    assert received.count(tid) == 1  # 只完成一次，没有重复执行


# ---------------------------------------------------------------------- #
# N45 事件总线：任务生命周期事件发布（旁路）
# ---------------------------------------------------------------------- #
def test_harness_publishes_lifecycle_events() -> None:
    """提交/开始/完成时向 EventBus 发布 agent.task 生命周期事件。"""
    from chuan.bus import EventBus

    bus = EventBus(backend="memory")
    pool = AgentPool(_FakeLoader())
    harness = AgentHarness(pool, bus=bus)
    harness._pool.register(_echo_agent())

    events: list[dict] = []
    bus.subscribe("agent.task", lambda t, e: events.append(e))

    tid = harness.submit("fake_agent", "事件任务")
    _wait_done(harness, tid)

    types = [e["type"] for e in events]
    assert "delegate.submitted" in types
    assert "delegate.started" in types
    assert "delegate.done" in types
    done = [e for e in events if e["type"] == "delegate.done"][0]
    assert done["payload"]["task_id"] == tid
    assert done["source"] == "harness:fake_agent"
    assert done["payload"]["agent"] == "fake_agent"
    assert done["payload"]["success"] is True


def test_harness_disabled_bus_is_noop() -> None:
    """未启用事件总线（默认 config）→ harness 照常工作，事件发布是旁路。"""
    from pathlib import Path

    from chuan.bus import EventBus

    cfg = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    bus = EventBus(backend="auto", config_path=str(cfg))
    pool = AgentPool(_FakeLoader())
    harness = AgentHarness(pool, bus=bus)
    harness._pool.register(_echo_agent())
    tid = harness.submit("fake_agent", "无总线任务")
    info = _wait_done(harness, tid)
    assert info["status"] == "done"  # bus 未启用时任务不受影响
