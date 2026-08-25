"""N28 例行自动化闭环：调度解析 / 每周调度 / RoutineManager / 归档 wiki / 管理接口。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from chuan.memory import Memory
from chuan.routines import RoutineManager, parse_schedule
from chuan.runtime_supervisor import RuntimeSupervisor
from chuan.scheduler import (
    ProactiveAlert,
    ProactiveScheduler,
    _next_weekly,
)


def _memory(tmp_path: Path) -> Memory:
    return Memory(vault_path=tmp_path / "vault")


def _scheduler(invoker=None) -> ProactiveScheduler:
    def _invoke(agent, message, session):
        return {"messages": [{"role": "assistant", "content": f"结果:{message}"}]}

    return ProactiveScheduler(invoker or _invoke)


def _d(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# --------------------------------------------------------------------- #
# 调度写法解析
# --------------------------------------------------------------------- #
def test_parse_weekly_space_and_compact() -> None:
    assert parse_schedule("fri 17:30") == ("weekly", (4, 17, 30))
    assert parse_schedule("fri@17:30") == ("weekly", (4, 17, 30))
    assert parse_schedule("周五 08:00") == ("weekly", (4, 8, 0))
    assert parse_schedule("周三@09:15") == ("weekly", (2, 9, 15))


def test_parse_interval_space_and_compact() -> None:
    assert parse_schedule("every 3600") == ("interval", 3600.0)
    assert parse_schedule("every@86400") == ("interval", 86400.0)


def test_parse_invalid_raises() -> None:
    for bad in ("someday 12:00", "fri 25:00", "fri 12:99", "every 0", "每周"):
        try:
            parse_schedule(bad)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝非法调度: {bad!r}")


# --------------------------------------------------------------------- #
# 每周调度时间计算
# --------------------------------------------------------------------- #
def test_next_weekly_advances_to_target_dow() -> None:
    now = _d(2026, 8, 24, 12, 0)
    nxt = _next_weekly(4, 17, 30, now)  # 目标周五 17:30
    assert nxt.weekday() == 4
    assert (nxt.hour, nxt.minute) == (17, 30)
    assert now < nxt <= now + timedelta(days=7)


def test_next_weekly_same_day_early_and_past() -> None:
    base = _d(2026, 8, 24, 12, 0)
    fri = base + timedelta(days=(4 - base.weekday()) % 7)  # 最近的周五 12:00
    # 周五 9:00 → 当天 17:30
    same = _next_weekly(4, 17, 30, fri.replace(hour=9))
    assert same == fri.replace(hour=17, minute=30)
    # 周五 18:00（已过）→ 下周周五 17:30（7 天差 30 分钟）
    late = _next_weekly(4, 17, 30, fri.replace(hour=18))
    assert late == fri.replace(hour=18) + timedelta(days=7) - timedelta(minutes=30)


# --------------------------------------------------------------------- #
# 调度器：每周任务触发 + 重新调度
# --------------------------------------------------------------------- #
def test_add_weekly_job_and_reschedule() -> None:
    sched = _scheduler()
    job = sched.add_weekly_job(
        "deploy_report", "帮我生成部署周报", 4, 17, 30, run_immediately=True)
    assert job.weekly == (4, 17, 30)
    assert job.next_run is not None

    alerts = sched.run_pending()
    assert len(alerts) == 1
    assert alerts[0].job_name == "deploy_report"
    assert "结果:帮我生成部署周报" in alerts[0].content

    # 已重新调度到下一周（周五 17:30），同刻不再触发
    rescheduled = sched.list_jobs()[0]
    assert rescheduled.next_run is not None
    assert rescheduled.next_run.weekday() == 4
    assert rescheduled.next_run.hour == 17
    assert sched.run_pending() == []


def test_on_routine_done_fires() -> None:
    got: list[ProactiveAlert] = []
    sched = _scheduler()
    sched._on_routine_done = got.append  # 直接注入回调（等价于构造参数）
    sched.add_interval_job("ping", "hi", interval_seconds=1, run_immediately=True)
    sched.run_pending()
    assert len(got) == 1 and got[0].job_name == "ping"


# --------------------------------------------------------------------- #
# N29 失败重试：退避 / 成功清零 / 耗尽告警 / 永久错误不重试
# --------------------------------------------------------------------- #
def _failing(exc: Exception = RuntimeError("boom")) -> object:
    def _invoke(agent, message, session):
        raise exc
    return _invoke


def test_retry_failure_enters_backoff_no_alert() -> None:
    sched = _scheduler(_failing())
    now = _d(2026, 8, 24, 12, 0)
    job = sched.add_interval_job("ping", "hi", interval_seconds=60, retries=1)
    job.next_run = now  # 固定触发时刻
    assert sched.run_pending(now=now) == []  # 瞬态失败 → 静默退避，不告警
    job = sched.list_jobs()[0]
    assert job.fail_count == 1
    assert job.next_run == now + timedelta(seconds=60)  # 退避基数 60s


def test_retry_success_resets_fail_count_and_alerts() -> None:
    calls = {"n": 0}

    def _flaky(agent, message, session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"messages": [{"role": "assistant", "content": "ok"}]}

    sched = _scheduler(_flaky)
    now = _d(2026, 8, 24, 12, 0)
    job = sched.add_interval_job("ping", "hi", interval_seconds=60, retries=1)
    job.next_run = now
    assert sched.run_pending(now=now) == []  # 首次失败 → 退避
    assert sched.list_jobs()[0].fail_count == 1
    retry_at = now + timedelta(seconds=60)
    alerts = sched.run_pending(now=retry_at)  # 到退避时刻重跑成功
    assert len(alerts) == 1 and alerts[0].error is False
    job = sched.list_jobs()[0]
    assert job.fail_count == 0  # 成功清零
    assert job.next_run == retry_at + timedelta(seconds=60)  # 下一间隔


def test_retry_exhausted_emits_error_and_reschedules() -> None:
    sched = _scheduler(_failing())
    now = _d(2026, 8, 24, 12, 0)
    job = sched.add_interval_job("ping", "hi", interval_seconds=60, retries=1)
    job.next_run = now
    assert sched.run_pending(now=now) == []  # 首次失败 → 退避
    retry_at = now + timedelta(seconds=60)
    alerts = sched.run_pending(now=retry_at)  # 重试仍失败 → 耗尽
    assert len(alerts) == 1 and alerts[0].error
    assert "[PROACTIVE JOB ERROR]" in alerts[0].content
    job = sched.list_jobs()[0]
    assert job.fail_count == 0  # 本轮结束清零，下个触发点重新开始
    assert job.next_run == retry_at + timedelta(seconds=60)


def test_permanent_error_skips_retry() -> None:
    sched = _scheduler(_failing(KeyError("worker 不存在")))
    now = _d(2026, 8, 24, 12, 0)
    job = sched.add_interval_job("ping", "hi", interval_seconds=60, retries=3)
    job.next_run = now
    alerts = sched.run_pending(now=now)  # 永久错误 → 不重试，立即告警
    assert len(alerts) == 1 and alerts[0].error
    job = sched.list_jobs()[0]
    assert job.fail_count == 0
    assert job.next_run == now + timedelta(seconds=60)


def test_retries_zero_backward_compatible() -> None:
    sched = _scheduler(_failing())
    now = _d(2026, 8, 24, 12, 0)
    job = sched.add_interval_job("ping", "hi", interval_seconds=60)  # 默认 retries=0
    job.next_run = now
    alerts = sched.run_pending(now=now)
    assert len(alerts) == 1 and alerts[0].error  # 不重试，直接告警


def test_empty_result_treated_as_failure_retryable() -> None:
    def _empty(agent, message, session):
        return {"messages": []}

    sched = _scheduler(_empty)
    now = _d(2026, 8, 24, 12, 0)
    job = sched.add_interval_job("ping", "hi", interval_seconds=60, retries=1)
    job.next_run = now
    assert sched.run_pending(now=now) == []  # 空结果视为失败 → 退避
    assert sched.list_jobs()[0].fail_count == 1


def test_routine_retries_persist_applied_and_surface(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    data_root = tmp_path / "routines.json"
    mgr = RoutineManager(memory, data_root=data_root)
    mgr.add("deploy", "任务", "fri 17:30", retries=2)

    # 磁盘真相持久化
    mgr2 = RoutineManager(memory, data_root=data_root)
    assert mgr2.get("deploy").retries == 2
    # apply_to 透传给调度器 + retry_state 暴露
    sched = _scheduler()
    mgr2.apply_to(sched)
    job = next(j for j in sched.list_jobs() if j.name == "deploy")
    assert job.retries == 2
    assert mgr2.retry_state(sched, "deploy") == {"retries": 2, "fail_count": 0}


def test_supervisor_routine_add_retries_surfaces(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    sup = _SupLike(memory, tmp_path / "routines.json")
    msg = sup.routine_add("deploy", "任务", "fri 17:30", retries=2)
    assert "重试 2 次" in msg
    item = sup.routine_list()[0]
    assert item["retries"] == 2 and item["fail_count"] == 0


# --------------------------------------------------------------------- #
# RoutineManager：增删查 + 持久化 + 应用调度
# --------------------------------------------------------------------- #
def test_routine_manager_add_remove_list_persist(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    data_root = tmp_path / "routines.json"
    mgr = RoutineManager(memory, data_root=data_root)
    mgr.add("deploy_report", "帮我生成部署周报", "fri 17:30", archive_to_wiki=True)
    mgr.add("heartbeat", "报个平安", "every 3600")
    assert {r.name for r in mgr.list()} == {"deploy_report", "heartbeat"}
    assert mgr.get("deploy_report").archive_to_wiki is True

    # 重新加载（磁盘真相）仍能读到
    mgr2 = RoutineManager(memory, data_root=data_root)
    assert {r.name for r in mgr2.list()} == {"deploy_report", "heartbeat"}

    assert mgr2.remove("heartbeat") is True
    assert mgr2.remove("heartbeat") is False  # 幂等
    assert [r.name for r in mgr2.list()] == ["deploy_report"]


def test_routine_add_invalid_schedule_rejected(tmp_path: Path) -> None:
    mgr = RoutineManager(_memory(tmp_path), data_root=tmp_path / "r.json")
    try:
        mgr.add("bad", "任务", "someday 12:00")
    except ValueError:
        pass
    else:
        raise AssertionError("非法调度应被拒绝且不落盘")
    assert mgr.list() == []


def test_apply_to_registers_weekly_and_interval(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    mgr = RoutineManager(memory, data_root=tmp_path / "routines.json")
    mgr.add("deploy_report", "帮我生成部署周报", "fri@17:30")
    mgr.add("heartbeat", "报个平安", "every@3600")

    sched = _scheduler()
    count = mgr.apply_to(sched)
    assert count == 2
    jobs = {j.name: j for j in sched.list_jobs()}
    assert jobs["deploy_report"].weekly == (4, 17, 30)
    assert jobs["heartbeat"].interval_seconds == 3600.0
    assert jobs["heartbeat"].weekly is None


# --------------------------------------------------------------------- #
# 归档 wiki 钩子
# --------------------------------------------------------------------- #
def _alert(job_name: str, content: str, error: bool = False) -> ProactiveAlert:
    return ProactiveAlert(job_name, "housekeeper", content, _d(2026, 8, 24, 12, 0), error)


def test_archive_routine_result_writes_wiki_source(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    mgr = RoutineManager(memory, data_root=tmp_path / "routines.json")
    mgr.add("deploy_report", "帮我生成部署周报", "fri 17:30", archive_to_wiki=True)
    sup = SimpleNamespace(memory=memory, routines=mgr)

    RuntimeSupervisor._archive_routine_result(sup, _alert("deploy_report", "本周部署完成，共 3 项变更"))
    src = memory.notes_path / "sources" / "routine-deploy_report.md"
    assert src.exists()
    assert "本周部署完成" in src.read_text(encoding="utf-8")


def test_archive_skips_error_and_non_archive(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    mgr = RoutineManager(memory, data_root=tmp_path / "routines.json")
    mgr.add("arch", "任务", "fri 17:30", archive_to_wiki=True)
    mgr.add("plain", "任务", "fri 17:30", archive_to_wiki=False)
    sup = SimpleNamespace(memory=memory, routines=mgr)

    # 错误提醒不归档
    RuntimeSupervisor._archive_routine_result(sup, _alert("arch", "出错", error=True))
    assert not (memory.notes_path / "sources" / "routine-arch.md").exists()

    # 未开 archive_to_wiki 的例行不归档
    RuntimeSupervisor._archive_routine_result(sup, _alert("plain", "普通结果"))
    assert not (memory.notes_path / "sources" / "routine-plain.md").exists()


# --------------------------------------------------------------------- #
# 幕僚长管理接口（最小替身）
# --------------------------------------------------------------------- #
class _SupLike(RuntimeSupervisor):
    def __init__(self, memory: Memory, data_root: Path) -> None:
        self.memory = memory
        self.routines = RoutineManager(memory, data_root=data_root)
        self.scheduler = ProactiveScheduler(
            lambda *a, **k: {"messages": [{"role": "assistant", "content": "ok"}]}
        )


def test_supervisor_routine_add_list_remove(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    sup = _SupLike(memory, tmp_path / "routines.json")

    msg = sup.routine_add("deploy_report", "帮我生成部署周报", "fri 17:30")
    assert "已添加例行任务「deploy_report」" in msg
    assert len(sup.routine_list()) == 1
    assert sup.routine_list()[0]["schedule"] == "fri 17:30"

    assert "已移除" in sup.routine_remove("deploy_report")
    assert sup.routine_list() == []
    assert "未找到" in sup.routine_remove("deploy_report")

    # 非法调度给可读提示，不崩溃
    assert "添加失败" in sup.routine_add("bad", "任务", "someday 12:00")
