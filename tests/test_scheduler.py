from datetime import timedelta

from chuan.scheduler import ProactiveScheduler


def test_due_job_invokes_named_worker_and_records_alert() -> None:
    calls: list[tuple[str, str, str]] = []

    def invoke(agent: str, message: str, session: str):
        calls.append((agent, message, session))
        return {"messages": [{"role": "assistant", "content": "记得补水"}]}

    scheduler = ProactiveScheduler(invoke)
    job = scheduler.add_interval_job(
        "hydration", "检查喝水提醒", interval_seconds=60, agent_name="housekeeper"
    )
    alerts = scheduler.run_pending(now=job.next_run)

    assert calls == [("housekeeper", "检查喝水提醒", "proactive:hydration")]
    assert alerts[0].content == "记得补水"
    assert scheduler.get_alerts(clear=True) == alerts
    assert scheduler.get_alerts() == []


def test_failed_job_becomes_alert_and_is_rescheduled() -> None:
    scheduler = ProactiveScheduler(lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    job = scheduler.add_interval_job("health", "检查", interval_seconds=30)
    now = job.next_run

    alert = scheduler.run_pending(now=now)[0]
    updated = scheduler.list_jobs()[0]
    assert alert.error is True
    assert "offline" in alert.content
    assert updated.run_count == 1
    assert updated.next_run == now + timedelta(seconds=30)
