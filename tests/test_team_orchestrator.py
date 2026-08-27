"""N42 岗位间协作 —— 多岗位并行编排 + 共享黑板测试。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace

from chuan.team_orchestrator import (
    TeamAssignment,
    TeamBlackboard,
    TeamOrchestrator,
    TeamPlan,
    detect_team_roles,
)

# ── 测试替身 ─────────────────────────────────────────


class FakeTeamRole:
    """记录调用、模拟真实岗位 dispatch 包装格式的假岗位。"""

    def __init__(self, name: str, display: str, reply: str = "产出",
                 fail: bool = False, delay: float = 0.0) -> None:
        self.name = name
        self.display_name = display
        self.reply = reply
        self.fail = fail
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    async def dispatch(self, task: str, session_id: str = "default",
                       aci_context: str = "") -> str:
        self.calls.append((task, session_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            return f"[{self.display_name}] 执行失败：{self.reply}"
        return f"[{self.display_name}] {self.reply}"


class BarrierRole(FakeTeamRole):
    """并发协调岗位：共享 barrier，所有并行实例到齐才放行（串行会超时失败）。"""

    def __init__(self, name: str, display: str, barrier: asyncio.Barrier) -> None:
        super().__init__(name, display)
        self.barrier = barrier

    async def dispatch(self, task: str, session_id: str = "default",
                       aci_context: str = "") -> str:
        try:
            await asyncio.wait_for(self.barrier.wait(), timeout=2.0)
        except (TimeoutError, asyncio.BrokenBarrierError):
            return f"[{self.display_name}] 串行超时"
        return f"[{self.display_name}] 并行完成"


class FakeSupervisor:
    """假幕僚长：worker 注册表 + 常驻事件循环线程（对齐真实 RuntimeSupervisor）。"""

    def __init__(self, workers: dict) -> None:
        self._workers = workers
        self._is_awake = True
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


_ROSTER = {"researcher": "研究", "copywriter": "文案", "secretary": "秘书"}


def _plan(*roles: str, task: str = "筹备产品发布会") -> TeamPlan:
    return TeamPlan(
        task=task,
        assignments=[
            TeamAssignment(role=r, mandate=_ROSTER[r], display=_ROSTER[r])
            for r in roles
        ],
    )


# ── detect_team_roles：确定性解析 ────────────────────


def test_detect_team_roles_parses_explicit_roles() -> None:
    plan = detect_team_roles("让研究、文案一起筹备发布会", _ROSTER)
    assert plan is not None
    assert plan.task == "筹备发布会"
    assert [a.role for a in plan.assignments] == ["researcher", "copywriter"]


def test_detect_team_roles_three_roles_with_he() -> None:
    plan = detect_team_roles("请让研究、文案和秘书一起完成市场分析", _ROSTER)
    assert plan is not None
    assert plan.task == "市场分析"
    assert {a.role for a in plan.assignments} == {
        "researcher", "copywriter", "secretary"
    }


def test_detect_team_roles_supports_english_and_connector_variants() -> None:
    plan = detect_team_roles("让 researcher 与 copywriter 协作写报告", _ROSTER)
    assert plan is not None
    assert [a.role for a in plan.assignments] == ["researcher", "copywriter"]


def test_detect_team_roles_single_role_returns_none() -> None:
    assert detect_team_roles("让研究帮我查资料", _ROSTER) is None  # 单岗位
    assert detect_team_roles("让研究、文案一起", _ROSTER) is None  # 无任务


def test_detect_team_roles_no_roster_match_returns_none() -> None:
    assert detect_team_roles("我们一起吃饭吧", _ROSTER) is None  # 无岗位命中
    assert detect_team_roles("帮我查天气", _ROSTER) is None  # 无连接词
    assert detect_team_roles("", _ROSTER) is None


# ── TeamBlackboard：共享黑板落盘 ─────────────────────


def test_blackboard_writes_context_and_results(tmp_path) -> None:
    bb = TeamBlackboard("筹备发布会", _plan("researcher", "copywriter").assignments,
                        session_id="sessA", root=tmp_path)
    bb.write_context()
    bb.write_result("researcher", "竞品调研完成", True)
    bb.write_result("copywriter", "文案初稿", False)
    context = (tmp_path / "teams" / "sessA" / "backboard" / "context.md").read_text(
        encoding="utf-8")
    assert "团队任务" in context and "筹备发布会" in context
    assert "研究" in context and "文案" in context
    r = (tmp_path / "teams" / "sessA" / "backboard" / "researcher.md").read_text(
        encoding="utf-8")
    assert "竞品调研完成" in r
    c = (tmp_path / "teams" / "sessA" / "backboard" / "copywriter.md").read_text(
        encoding="utf-8")
    assert "（失败）" in c


def test_blackboard_colon_session_id_safe_on_windows(tmp_path) -> None:
    """session id 含冒号（团队会话如 team:demo）→ 目录名不含冒号（Windows 合法）。"""
    bb = TeamBlackboard("t", _plan("researcher").assignments,
                        session_id="team:demo", root=tmp_path)
    bb.write_context()
    # 关键目录组件不含冒号（Windows 目录名不允许冒号；绝对路径里的盘符冒号除外）
    assert bb.dir.parent.name == "team_demo" and bb.dir.name == "backboard"
    assert (tmp_path / "teams" / "team_demo" / "backboard" / "context.md").exists()


# ── TeamOrchestrator.orchestrate：并行派发 + 汇总 ────


def _orch(workers: dict, root) -> TeamOrchestrator:
    sup = FakeSupervisor(workers)
    orch = TeamOrchestrator(sup, root=root)
    return orch


def test_orchestrate_dispatches_all_roles_with_shared_context(tmp_path) -> None:
    researcher = FakeTeamRole("researcher", "研究", reply="竞品调研")
    copywriter = FakeTeamRole("copywriter", "文案", reply="文案初稿")
    orch = _orch({"researcher": researcher, "copywriter": copywriter}, tmp_path)
    summary = orch.orchestrate(_plan("researcher", "copywriter"), session_id="sessA")

    assert len(researcher.calls) == 1 and len(copywriter.calls) == 1
    # 各自拿到团队总任务 + 分工 + 黑板
    for role in (researcher, copywriter):
        prompt = role.calls[0][0]
        assert "【团队任务】" in prompt and "筹备产品发布会" in prompt
        assert "【团队分工】" in prompt and "【黑板】" in prompt
        assert role.calls[0][1].startswith("team_sessA_")  # 独立协作会话
    # 汇总分节 + 剥离岗位前缀
    assert "### [研究]" in summary and "竞品调研" in summary
    assert "### [文案]" in summary and "文案初稿" in summary
    assert "团队协作完成" in summary
    # 黑板落盘
    assert (tmp_path / "teams" / "sessA" / "backboard" / "researcher.md").exists()
    assert (tmp_path / "teams" / "sessA" / "backboard" / "copywriter.md").exists()


def test_orchestrate_runs_roles_in_parallel(tmp_path) -> None:
    """两个岗位必须真并行：串行执行会因共享 barrier 超时而失败。"""
    barrier = asyncio.Barrier(2)
    a = BarrierRole("researcher", "研究", barrier)
    b = BarrierRole("copywriter", "文案", barrier)
    orch = _orch({"researcher": a, "copywriter": b}, tmp_path)
    summary = orch.orchestrate(_plan("researcher", "copywriter"), session_id="par")
    assert "并行完成" in summary
    assert "串行超时" not in summary and "执行失败" not in summary


def test_orchestrate_failure_of_one_does_not_block_others(tmp_path) -> None:
    good = FakeTeamRole("researcher", "研究", reply="正常产出")
    bad = FakeTeamRole("copywriter", "文案", fail=True, reply="写砸了")
    orch = _orch({"researcher": good, "copywriter": bad}, tmp_path)
    summary = orch.orchestrate(_plan("researcher", "copywriter"), session_id="mix")
    assert "正常产出" in summary
    assert "写砸了" in summary and "（失败）" in summary
    assert len(good.calls) == 1 and len(bad.calls) == 1


def test_orchestrate_missing_role_marks_failed(tmp_path) -> None:
    only = FakeTeamRole("researcher", "研究", reply="正常产出")
    orch = _orch({"researcher": only}, tmp_path)
    summary = orch.orchestrate(
        _plan("researcher", "secretary"), session_id="miss")
    assert "正常产出" in summary
    assert "secretary 不可用" in summary and "（失败）" in summary


def test_await_result_preserves_non_role_bracket_content(tmp_path) -> None:
    """_await_result 只剥「[角色名]」前缀，不误伤产出正文里的方括号标签。"""
    researcher = FakeTeamRole("researcher", "研究", reply="[数据] 1.北京 2.上海")
    orch = _orch({"researcher": researcher}, tmp_path)
    summary = orch.orchestrate(_plan("researcher"), session_id="bracket")
    # 「[数据]」标签必须保留在产出正文中
    assert "[数据]" in summary
    # 角色包装前缀「[研究]」必须被剥掉（不重复出现在产出正文行）
    # summary 的分节标题是「### [研究]」，产出正文行不应再以「[研究]」开头
    lines = summary.splitlines()
    content_lines = [l for l in lines if l.strip() and not l.startswith("###")]
    assert any("[数据]" in l for l in content_lines)


# ── plan_team_llm：LLM 选岗拆分（/team）──────────────


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        return SimpleNamespace(content=self.content)


def _plan_from_model(workers: dict, model, root) -> TeamPlan | None:
    orch = _orch(workers, root)
    return orch.plan_team_llm("筹备产品发布会", model)


def test_plan_team_llm_valid_json_selects_roles(tmp_path) -> None:
    workers = {n: FakeTeamRole(n, d) for n, d in _ROSTER.items()}
    model = FakeModel(
        '{"task": "筹备产品发布会", "assignments": ['
        '{"role": "researcher", "mandate": "调研竞品"},'
        '{"role": "copywriter", "mandate": "写文案"}]}'
    )
    plan = _plan_from_model(workers, model, tmp_path)
    assert plan is not None
    assert plan.task == "筹备产品发布会"
    assert [a.role for a in plan.assignments] == ["researcher", "copywriter"]
    assert plan.assignments[0].mandate == "调研竞品"


def test_plan_team_llm_filters_unknown_roles(tmp_path) -> None:
    workers = {n: FakeTeamRole(n, d) for n, d in _ROSTER.items()}
    model = FakeModel(
        '{"task": "t", "assignments": ['
        '{"role": "researcher", "mandate": "a"},'
        '{"role": "不存在的岗位", "mandate": "b"}]}'
    )
    plan = _plan_from_model(workers, model, tmp_path)
    # 未知岗位被过滤；只剩 1 个 → 不足 2 岗 → None
    assert plan is None


def test_plan_team_llm_garbage_returns_none(tmp_path) -> None:
    workers = {n: FakeTeamRole(n, d) for n, d in _ROSTER.items()}
    for bad in ("我不拆", "{}", "no json"):
        assert _plan_from_model(workers, FakeModel(bad), tmp_path) is None


def test_plan_team_llm_no_model_returns_none(tmp_path) -> None:
    workers = {n: FakeTeamRole(n, d) for n, d in _ROSTER.items()}
    orch = _orch(workers, tmp_path)
    assert orch.plan_team_llm("任务", None) is None


# ── 并发/状态机排雷（c 超时取消 + d1 防重入）────────


class _TimeoutFuture:
    """模拟 result(timeout) 超时、cancel 被调用的 future。"""

    def __init__(self) -> None:
        self.cancelled = False

    def result(self, timeout):
        raise concurrent.futures.TimeoutError("timed out")

    def cancel(self) -> bool:
        self.cancelled = True
        return True


class _ErrorFuture:
    """模拟 result(timeout) 抛普通异常的 future。"""

    def __init__(self) -> None:
        self.cancelled = False

    def result(self, timeout):
        raise RuntimeError("boom")

    def cancel(self) -> bool:
        self.cancelled = True
        return True


def _assignment() -> TeamAssignment:
    return TeamAssignment(role="researcher", mandate="研究", display="研究")


def test_await_result_timeout_cancels_future(tmp_path) -> None:
    orch = TeamOrchestrator(None, root=tmp_path)
    fut = _TimeoutFuture()
    content, success = orch._await_result(_assignment(), fut)
    assert success is False
    assert "执行超时" in content and "已取消" in content
    assert fut.cancelled is True


def test_await_result_error_does_not_cancel(tmp_path) -> None:
    orch = TeamOrchestrator(None, root=tmp_path)
    fut = _ErrorFuture()
    content, success = orch._await_result(_assignment(), fut)
    assert success is False
    assert "执行失败" in content and "执行超时" not in content
    assert fut.cancelled is False


def test_orchestrate_reentrant_same_session_rejected(tmp_path) -> None:
    researcher = FakeTeamRole("researcher", "研究", reply="产出")
    orch = _orch({"researcher": researcher}, tmp_path)
    orch._running.add("sessA")  # 预置「正在执行」
    summary = orch.orchestrate(_plan("researcher"), session_id="sessA")
    assert "正在协作中" in summary
    assert len(researcher.calls) == 0  # 未重复派发


def test_orchestrate_clears_running_after_run(tmp_path) -> None:
    researcher = FakeTeamRole("researcher", "研究", reply="产出")
    orch = _orch({"researcher": researcher}, tmp_path)
    orch.orchestrate(_plan("researcher"), session_id="sessA")
    assert "sessA" not in orch._running
