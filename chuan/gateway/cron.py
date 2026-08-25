"""⑦ Cron —— 定时任务与主动提醒推送。

职责：从全局配置加载显式启用的定时管家任务，并启动后台轮询。
从 RuntimeSupervisor 迁移而来（ADR-012 Gateway 拆分）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor


class CronManager:
    """定时任务加载与调度启动。"""

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    def load_scheduled_jobs(self) -> None:
        """从全局配置加载显式启用的主动任务。"""
        path = Path(self._sup.config_path)
        if not path.is_absolute():
            # 本文件位于 chuan/gateway/cron.py，向上三级即项目根
            path = Path(__file__).resolve().parent.parent.parent / path
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as file:
            settings = (yaml.safe_load(file) or {}).get("scheduler", {})
        if not settings.get("enabled", False):
            return

        scheduler = self._sup.scheduler
        for job in settings.get("jobs", []):
            try:
                scheduler.add_interval_job(
                    job["name"],
                    job["message"],
                    interval_seconds=float(job["interval_seconds"]),
                    agent_name=job.get("agent", "housekeeper"),
                    run_immediately=bool(job.get("run_immediately", False)),
                )
            except (KeyError, TypeError, ValueError):
                continue
        scheduler.start(
            poll_interval_seconds=float(settings.get("poll_interval_seconds", 1.0))
        )