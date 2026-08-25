"""P1 监督者（SupervisorMonitor）—— 轨迹记录 + 死胡同检测 + redirect 决策测试。

全确定性设计：不依赖 LLM，阈值可注入，纯内存，单线程可测。
覆盖：
- 轨迹生命周期（start/record/finish/snapshot/裁剪）
- 死胡同三类判定：循环 / 反复失败 / 停滞
- redirect 决策：abort / switch_agent / inject_hint
- redirect 记录与面板快照
"""

from __future__ import annotations

import time

from chuan.gateway.supervisor_monitor import (
    RedirectDecision,
    SupervisorMonitor,
)


def _monitor(**kw) -> SupervisorMonitor:
    # 测试统一用小阈值，快速触发判定
    defaults = dict(
        fail_threshold=2,
        loop_threshold=2,
        max_fail_attempts=3,
        stagnation_secs=0.5,
    )
    defaults.update(kw)
    return SupervisorMonitor(**defaults)


def _record(
    m: SupervisorMonitor,
    trace: str,
    step: str,
    *,
    attempt: int,
    agent: str = "pi",
    success: bool = False,
    content: str = "结果 A",
) -> None:
    m.record_step(
        trace, step, attempt=attempt, agent=agent,
        success=success, content=content, duration=0.1,
    )


# ---------------------------------------------------------------------- #
# 轨迹生命周期
# ---------------------------------------------------------------------- #
def test_trace_lifecycle_and_snapshot() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, success=True, content="完成了")
    m.finish_trace("t1")

    snap = m.snapshot()
    assert snap["stats"]["traces"] == 1
    assert snap["stats"]["active"] == 0
    tr = snap["traces"][0]
    assert tr["trace_id"] == "t1"
    assert tr["role"] == "工程师"
    assert tr["steps"] == 1
    assert tr["status"] == "done"
    assert tr["last_step"] == "s1"
    assert tr["last_ok"] is True


def test_start_trace_is_idempotent() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    m.start_trace("工程师", "t1")  # 重复开启复用，不重置
    assert len(m._traces["t1"]["steps"]) == 0
    m.finish_trace("t1")


def test_record_unknown_trace_is_noop() -> None:
    m = _monitor()
    _record(m, "ghost", "s1", attempt=0, content="x")  # 不应抛异常
    assert m.snapshot()["stats"]["traces"] == 0


def test_finished_trace_trimmed_beyond_limit() -> None:
    m = _monitor(max_traces=2)
    for i in range(3):
        m.start_trace("工程师", f"t{i}")
        m.finish_trace(f"t{i}")
    # 活跃轨迹不丢：再开一条活跃的验证
    m.start_trace("工程师", "alive")
    traces = {t["trace_id"] for t in m.snapshot()["traces"]}
    assert "alive" in traces
    assert len(traces) <= 2


# ---------------------------------------------------------------------- #
# 死胡同判定 —— 反复失败（结果相似）
# ---------------------------------------------------------------------- #
def test_repeated_failure_similar_results_aborts_without_candidates() -> None:
    m = _monitor(max_fail_attempts=5)
    m.start_trace("工程师", "t1")
    # 连续两次失败 + 内容中度相似（≥0.7 但 <0.95，避开循环判定）→ 反复失败
    _record(m, "t1", "s1", attempt=0, content="失败原因网络连接超时重试")
    _record(m, "t1", "s1", attempt=1, content="失败原因数据库连接超时重试")

    d = m.check_dead_end("t1", "s1", retries_left=1, current_agent="pi", available=[])
    assert d is not None
    assert d.kind == "inject_hint"  # 反复失败且无候选 agent、还有次数 → 注入思路
    assert d.hint

    # 有候选 agent → 换 agent（即使只剩最后一次）
    d2 = m.check_dead_end("t1", "s1", retries_left=0, current_agent="pi",
                          available=["opencode"])
    assert d2 is not None and d2.kind == "switch_agent"


def test_repeated_failure_switches_agent_when_available() -> None:
    m = _monitor(max_fail_attempts=5)
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, content="失败原因网络连接超时重试")
    _record(m, "t1", "s1", attempt=1, content="失败原因数据库连接超时重试")

    d = m.check_dead_end("t1", "s1", retries_left=3, current_agent="pi",
                         available=["opencode", "claude_code"])
    assert d is not None
    assert d.kind == "switch_agent"
    assert d.target_agent == "opencode"  # 取第一个非当前 agent


def test_failures_without_similarity_not_flagged_early() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, content="报错 A")
    _record(m, "t1", "s1", attempt=1, content="报错 B 完全不同的失败")
    # 连续 2 次失败但内容不相似 → 不误判
    assert m.check_dead_end("t1", "s1", retries_left=1, current_agent="pi",
                            available=[]) is None


def test_exhausted_attempts_flagged() -> None:
    m = _monitor(max_fail_attempts=3)
    m.start_trace("工程师", "t1")
    # 三次失败内容各不相同（不触发相似判定），纯靠尝试耗尽判死
    _record(m, "t1", "s1", attempt=0, content="第一次尝试失败")
    _record(m, "t1", "s1", attempt=1, content="失败二原因完全不同")
    _record(m, "t1", "s1", attempt=2, content="失败三换了新的方法")
    d = m.check_dead_end("t1", "s1", retries_left=0, current_agent="pi",
                         available=[])
    assert d is not None
    assert d.kind == "abort"  # 尝试耗尽且无候选 → 不再空耗
    assert "耗尽" in d.reason


# ---------------------------------------------------------------------- #
# 死胡同判定 —— 循环（输出高度相似）
# ---------------------------------------------------------------------- #
def test_loop_detected_and_aborts_when_no_agent() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, success=False, content="失败：内存不足")
    _record(m, "t1", "s1", attempt=1, success=False, content="失败：内存不足，需扩容")

    d = m.check_dead_end("t1", "s1", retries_left=1, current_agent="pi", available=[])
    assert d is not None
    assert d.kind == "abort"  # 循环且无候选 agent → 直接中止，不再同 prompt 空耗
    assert "循环" in d.reason


def test_loop_switches_agent_when_available() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, content="输出相同结论 X")
    _record(m, "t1", "s1", attempt=1, content="输出相同结论 X 几乎一致")

    d = m.check_dead_end("t1", "s1", retries_left=3, current_agent="pi",
                         available=["opencode"])
    assert d is not None
    assert d.kind == "switch_agent"
    assert d.target_agent == "opencode"


# ---------------------------------------------------------------------- #
# 死胡同判定 —— 停滞（watchdog）
# ---------------------------------------------------------------------- #
def test_stagnation_detected_after_timeout() -> None:
    m = _monitor(stagnation_secs=0.05)
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, success=False, content="第一次失败")
    time.sleep(0.1)
    d = m.check_dead_end("t1", "s1", retries_left=1, current_agent="pi",
                         available=[])
    assert d is not None
    assert d.kind == "abort"  # 单步长期悬挂（停滞）→ 无候选 agent 直接中止
    assert "停滞" in d.reason


def test_no_dead_end_when_only_one_step() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, success=False, content="第一次失败")
    # 单次尝试信号不足，绝不误判
    assert m.check_dead_end("t1", "s1", retries_left=2, current_agent="pi",
                            available=[]) is None


# ---------------------------------------------------------------------- #
# redirect 决策细节
# ---------------------------------------------------------------------- #
def test_switch_excludes_current_agent() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, content="相同输出")
    _record(m, "t1", "s1", attempt=1, content="相同输出 略")
    d = m.check_dead_end("t1", "s1", retries_left=3, current_agent="opencode",
                         available=["opencode", "pi"])
    assert d is not None and d.kind == "switch_agent"
    assert d.target_agent == "pi"  # 跳过当前 opencode


def test_redirect_recorded_and_snapshotted() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, content="相同输出")
    _record(m, "t1", "s1", attempt=1, content="相同输出 略")
    d = m.check_dead_end("t1", "s1", retries_left=3, current_agent="pi",
                         available=["opencode"])
    assert d is not None
    m.record_redirect("t1", "s1", d)

    snap = m.snapshot()
    assert snap["stats"]["redirects"] == 1
    assert snap["stats"]["dead_ends"] == 1
    rd = snap["redirects"][0]
    assert rd["kind"] == "switch_agent"
    assert rd["target_agent"] == "opencode"
    de = snap["dead_ends"][0]
    assert de["kind"] == "loop"
    assert de["redirect"] == "switch_agent"


def test_record_redirect_noop_for_clean_step() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, success=True, content="完成")
    assert m.check_dead_end("t1", "s1", retries_left=1, current_agent="pi",
                            available=[]) is None
    assert m.snapshot()["stats"]["dead_ends"] == 0


# ---------------------------------------------------------------------- #
# 相似度判定（确定性边界）
# ---------------------------------------------------------------------- #
def test_similarity_empty_both_requires_exact() -> None:
    assert SupervisorMonitor._similar("", "", 0.9) is True
    assert SupervisorMonitor._similar("", "非空", 0.9) is False


def test_similarity_cjk_partial_overlap() -> None:
    # 小集合被大集合覆盖（同一结果 + 额外废话）→ 相似
    assert SupervisorMonitor._similar("内存不足", "内存不足，建议扩容后重试", 0.7) is True
    # 完全不同 → 不相似
    assert SupervisorMonitor._similar("内存不足", "网络超时重连", 0.7) is False


# ---------------------------------------------------------------------- #
# 旁路保障：监控异常不阻断调用方
# ---------------------------------------------------------------------- #
def test_monitor_none_shortcircuit_via_role() -> None:
    """_check_dead_end/_record_step 在无 monitor 时直接返回，不抛错。"""
    from chuan.role import PersonaRole

    role = PersonaRole.__new__(PersonaRole)  # 跳过 __init__
    role._monitor = None
    role.pool = None
    role.name = "工程师"
    assert role._check_dead_end("t1", "s1", 1, "pi") is None
    role._record_step("t1", "s1", 0, "pi", False, "x")  # 不应抛异常
    role._begin_trace("t1")
    role._finish_trace("t1")


def test_redirect_decision_fields() -> None:
    d = RedirectDecision("abort", "原因", target_agent="x")
    assert d.is_abort is True
    assert RedirectDecision("switch_agent", "r", target_agent="y").is_abort is False


# ---------------------------------------------------------------------- #
# HUD 快照（hud_summary，供 HUD 面板展示的精简数据）
# ---------------------------------------------------------------------- #
def test_hud_summary_compact_stats_and_latest_dead() -> None:
    m = _monitor()
    m.start_trace("工程师", "t1")
    _record(m, "t1", "s1", attempt=0, success=True, content="完成了")

    m.start_trace("管家", "t2")
    _record(m, "t2", "s1", attempt=0, success=False, content="错误A")
    _record(m, "t2", "s1", attempt=1, success=False, content="错误B")
    _record(m, "t2", "s1", attempt=2, success=False, content="错误C")
    # 3 次不同内容失败（互不相似，避开循环判定）→ 尝试耗尽 → 反复失败死胡同
    decision = m.check_dead_end(
        "t2", "s1", retries_left=0, current_agent="pi",
    )
    assert decision is not None
    m.record_redirect("t2", "s1", decision)

    summary = m.hud_summary()
    stats = summary["stats"]
    assert stats["traces"] == 2
    assert stats["active"] == 2
    assert stats["dead_ends"] == 1
    assert stats["redirects"] == 1

    # 只带最近 3 条轨迹的精简字段
    assert len(summary["top_traces"]) == 2
    tr = summary["top_traces"][0]
    assert set(tr) == {
        "trace_id", "role", "steps", "active", "elapsed", "last_step", "last_ok",
    }
    # latest_dead 携带最近死胡同的关键信息
    dead = summary["latest_dead"]
    assert dead is not None
    assert dead["trace_id"] == "t2"
    assert dead["kind"] == "repeated_failure"
    assert dead["redirect"] == decision.kind


def test_hud_summary_no_data() -> None:
    m = _monitor()
    summary = m.hud_summary()
    assert summary["stats"]["traces"] == 0
    assert summary["stats"]["active"] == 0
    assert summary["top_traces"] == []
    assert summary["latest_dead"] is None

