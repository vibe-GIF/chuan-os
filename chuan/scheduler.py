"""N8 管家主动触发 —— 轻量本地定时任务与提醒队列。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class ScheduledJob:
    """一项定时交由指定 worker 执行的主动任务。

    ``weekly`` 非空时按每周 (day_of_week, hour, minute) 触发（周一=0）；
    为空时按 ``interval_seconds`` 间隔触发。二者互斥。
    """

    name: str
    message: str
    interval_seconds: float
    agent_name: str = "housekeeper"
    next_run: datetime | None = None
    run_count: int = 0
    weekly: tuple[int, int, int] | None = None
    # N29 失败重试：0=关闭（向后兼容）；其余为瞬态失败时的最大重试次数。
    # fail_count 只在「本轮触发→结算」窗口内累计，成功或最终失败后清零。
    retries: int = 0
    fail_count: int = 0
    retry_base: float = 60.0    # 退避基数（秒），指数增长
    retry_factor: float = 2.0   # 退避系数
    retry_max: float = 1800.0   # 退避封顶（秒），防永久坏任务长期占用


@dataclass(frozen=True)
class ProactiveAlert:
    """一次主动任务的可展示结果。"""

    job_name: str
    agent_name: str
    content: str
    created_at: datetime
    error: bool = False


WorkerInvoker = Callable[[str, str, str], dict[str, Any]]
AlertHandler = Callable[[ProactiveAlert], None]


def _next_weekly(
    day_of_week: int, hour: int, minute: int, now: datetime
) -> datetime:
    """计算下一个 (day_of_week, hour, minute) 时刻（周一=0；已过则顺延一周）。"""
    days_ahead = (day_of_week - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _retry_backoff(job: ScheduledJob, attempt: int) -> timedelta:
    """第 attempt 次重试的退避时长：指数、封顶、确定性（无抖动）。

    attempt=1 为首退，等 retry_base；之后每次乘 retry_factor，封顶 retry_max。
    """
    delay = job.retry_base * (job.retry_factor ** (attempt - 1))
    return timedelta(seconds=min(delay, job.retry_max))


class ProactiveScheduler:
    """不依赖外部服务的间隔调度器。

    调度器仅负责编排和保留提醒；具体通知（CLI、PWA、系统通知）由
    ``on_alert`` 注入。每项任务使用独立 ``proactive:<job>`` 会话。
    """

    def __init__(
        self,
        invoke_worker: WorkerInvoker,
        *,
        on_alert: AlertHandler | None = None,
        on_routine_done: AlertHandler | None = None,
    ) -> None:
        self._invoke_worker = invoke_worker
        self._on_alert = on_alert
        # N28 例行任务完成回调（供归档 wiki 等旁路使用；错误提醒也会触发）
        self._on_routine_done = on_routine_done
        self._jobs: dict[str, ScheduledJob] = {}
        self._alerts: list[ProactiveAlert] = []
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def add_interval_job(
        self,
        name: str,
        message: str,
        *,
        interval_seconds: float,
        agent_name: str = "housekeeper",
        run_immediately: bool = False,
        retries: int = 0,
    ) -> ScheduledJob:
        """新增或替换一项间隔任务。``retries`` 为瞬态失败时的最大重试次数（0=关闭）。"""
        if not name or not message.strip():
            raise ValueError("job name and message are required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        now = datetime.now(UTC)
        job = ScheduledJob(
            name=name,
            message=message,
            interval_seconds=interval_seconds,
            agent_name=agent_name,
            next_run=now if run_immediately else now + timedelta(seconds=interval_seconds),
            retries=max(int(retries), 0),
        )
        with self._lock:
            self._jobs[name] = job
        return job

    def add_weekly_job(
        self,
        name: str,
        message: str,
        day_of_week: int,
        hour: int,
        minute: int,
        *,
        agent_name: str = "housekeeper",
        run_immediately: bool = False,
        retries: int = 0,
    ) -> ScheduledJob:
        """新增或替换一项每周定时任务（周一=0；每周 day_of_week 的 hour:minute 触发）。

        ``retries`` 为瞬态失败时的最大重试次数（0=关闭）。
        """
        if not name or not message.strip():
            raise ValueError("job name and message are required")
        if not 0 <= day_of_week <= 6:
            raise ValueError("day_of_week must be 0-6 (Mon-Sun)")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("invalid time-of-day")
        now = datetime.now(UTC)
        job = ScheduledJob(
            name=name,
            message=message,
            interval_seconds=0.0,
            agent_name=agent_name,
            weekly=(day_of_week, hour, minute),
            next_run=(
                now
                if run_immediately
                else _next_weekly(day_of_week, hour, minute, now)
            ),
            retries=max(int(retries), 0),
        )
        with self._lock:
            self._jobs[name] = job
        return job

    def load_from_config(self, config: dict) -> int:
        """从配置字典批量注册间隔任务。

        读取 ``config["scheduler"]["jobs"]``，每项字段：
        ``name`` / ``message`` / ``interval_seconds`` / ``agent``（可选，默认
        ``housekeeper``）/ ``run_immediately``（可选，默认 ``False``）。
        当 ``config["scheduler"]["enabled"]`` 为假时不加载任何任务。

        Returns:
            实际注册的任务数量。
        """
        scheduler_cfg = config.get("scheduler", {}) or {}
        if not scheduler_cfg.get("enabled", False):
            return 0
        jobs = scheduler_cfg.get("jobs", []) or []
        count = 0
        for item in jobs:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            message = item.get("message")
            interval = item.get("interval_seconds")
            if not name or not message or interval is None:
                continue
            self.add_interval_job(
                name=str(name),
                message=str(message),
                interval_seconds=float(interval),
                agent_name=str(item.get("agent", "housekeeper")),
                run_immediately=bool(item.get("run_immediately", False)),
                retries=int(item.get("retries", 0) or 0),
            )
            count += 1
        return count

    def format_alerts(self) -> str:
        """读取并清空提醒队列，格式化为多行文本。

        每条提醒占两行：首行 ``[任务名 @ agent] 时间 [ERROR]``，次行为内容；
        多条之间用 ``---`` 分隔。队列为空时返回空字符串。
        """
        alerts = self.get_alerts(clear=True)
        if not alerts:
            return ""
        lines: list[str] = []
        for alert in alerts:
            header = f"[{alert.job_name} @ {alert.agent_name}] {alert.created_at.isoformat()}"
            if alert.error:
                header += " [ERROR]"
            lines.append(header)
            lines.append(alert.content)
            lines.append("---")
        # 去掉最后一条多余的分隔线
        if lines and lines[-1] == "---":
            lines.pop()
        return "\n".join(lines)

    def remove_job(self, name: str) -> bool:
        """移除任务，返回是否实际移除。"""
        with self._lock:
            return self._jobs.pop(name, None) is not None

    def list_jobs(self) -> list[ScheduledJob]:
        with self._lock:
            return [ScheduledJob(**job.__dict__) for job in self._jobs.values()]

    def get_alerts(self, *, clear: bool = False) -> list[ProactiveAlert]:
        """读取提醒队列；可选地在读取后清空。"""
        with self._lock:
            alerts = list(self._alerts)
            if clear:
                self._alerts.clear()
            return alerts

    def run_pending(self, *, now: datetime | None = None) -> list[ProactiveAlert]:
        """同步执行到期任务，方便 CLI 主循环和测试驱动。"""
        current = now or datetime.now(UTC)
        with self._lock:
            due_jobs = [
                job
                for job in self._jobs.values()
                if job.next_run and job.next_run <= current
            ]

        created: list[ProactiveAlert] = []
        for job in due_jobs:
            alert = self._run_job(job, current)
            if alert is None:
                continue  # 进入退避重试，暂不告警
            created.append(alert)
            self._record_alert(alert)
        return created

    def start(self, *, poll_interval_seconds: float = 1.0) -> None:
        """在后台线程启动调度；重复调用不会创建第二个线程。"""
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                args=(poll_interval_seconds,),
                name="chuan-proactive-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """停止后台线程；不会中断正在执行的 worker。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            if thread is self._thread and (thread is None or not thread.is_alive()):
                self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self, poll_interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            self.run_pending()
            self._stop_event.wait(poll_interval_seconds)

    def _run_job(self, job: ScheduledJob, now: datetime) -> ProactiveAlert | None:
        """执行到期任务；返回最终提醒，``None`` 表示已进入退避重试（暂不告警）。

        失败分级（N29）：
        - 瞬态（除 worker 缺失外的异常 + 退化内容）→ 有重试余量则指数退避重排，静默；
        - 永久（worker 不存在）→ 重试无意义，直接按最终失败结算；
        - 成功 / 重试耗尽 → 结束本轮，正常重排到下一个触发点。
        """
        permanent = False
        try:
            result = self._invoke_worker(
                job.agent_name, job.message, f"proactive:{job.name}"
            )
            content = self._result_text(result)
            error = self._is_failed_content(content)
        except KeyError:
            # worker 不存在是配置错误，重试多少次都不会好
            content = "[PROACTIVE JOB ERROR] worker 不可用"
            error = True
            permanent = True
        except Exception as exc:  # noqa: BLE001 - a failed job must not stop the scheduler
            content = f"[PROACTIVE JOB ERROR] {exc}"
            error = True
            permanent = False

        with self._lock:
            current = self._jobs.get(job.name)
            if current is None:
                return ProactiveAlert(job.name, job.agent_name, content, now, error)
            current.run_count += 1
            if (
                error
                and not permanent
                and current.retries > 0
                and current.fail_count < current.retries
            ):
                current.fail_count += 1
                current.next_run = now + _retry_backoff(current, current.fail_count)
                return None
            # 成功 / 永久错误 / 重试耗尽 → 结束本轮，正常重排
            current.fail_count = 0
            if current.weekly is not None:
                current.next_run = _next_weekly(*current.weekly, now)
            else:
                current.next_run = now + timedelta(seconds=current.interval_seconds)
        return ProactiveAlert(job.name, job.agent_name, content, now, error)

    @staticmethod
    def _is_failed_content(content: str) -> bool:
        """确定性退化判定：空回复 / 占位符 / 错误标记都视为失败，可重试。"""
        text = str(content or "").strip()
        if not text or text == "[PROACTIVE JOB COMPLETED]":
            return True
        if text.startswith("[PROACTIVE JOB ERROR]"):
            return True
        return False

    def _record_alert(self, alert: ProactiveAlert) -> None:
        with self._lock:
            self._alerts.append(alert)
        if self._on_alert is not None:
            self._on_alert(alert)
        # N28 例行完成旁路回调（错误提醒也会触发，由订阅方自行判断）
        if self._on_routine_done is not None:
            self._on_routine_done(alert)

    @staticmethod
    def _result_text(result: dict[str, Any]) -> str:
        messages = result.get("messages", [])
        if not messages:
            return "[PROACTIVE JOB COMPLETED]"
        last = messages[-1]
        content = getattr(last, "content", None)
        if content is None and isinstance(last, dict):
            content = last.get("content")
        return str(content or "[PROACTIVE JOB COMPLETED]")
