"""N28 例行任务（routine）—— 把 howto 原子 + wiki 串成「系统自转」闭环。

真用户故事：**每周五 17:30 自动出部署周报** —— scheduler 到点触发 →
``PersonaRole`` 开工自动注入「部署周报」howto 参考做法（N26）→ 跑完自动
提炼新候选待人工确认（N27）→（可选）结果归档 wiki ``sources/`` 原料层供
每日 ingest 归位（N24）→ 下周五复用已改进的原子。周而复始，知识库随使用
生长——系统开始自转而不是等召唤。

``Routine`` 是「命名 + 调度 + 任务」的一等概念，持久化到 ``data/routines.json``
（磁盘真相，重启不丢）；运行时经 CLI/TUI ``/routine`` 管理。

调度写法（``RoutineManager.add`` 的 ``schedule`` 参数）：
- ``"fri 17:30"`` / ``"fri@17:30"`` —— 每周五 17:30（dow 支持 mon..sun / 周一..周日）
- ``"every 3600"`` / ``"every@3600"`` —— 每 3600 秒

设计约束（与项目一致）：确定性、纯本地；调度解析不依赖任何模型；旁路。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from chuan.scheduler import ProactiveScheduler, _next_weekly

# 星期名 → 0=周一 .. 6=周日
_DOW_NAMES: dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6,
}


@dataclass
class Routine:
    """一项命名例行任务。"""

    name: str
    message: str
    schedule: str  # 原始调度写法，如 "fri 17:30" / "every 3600"
    agent: str = "housekeeper"
    archive_to_wiki: bool = False  # 完成后结果归档 wiki sources/ 原料层
    retries: int = 0  # N29 瞬态失败重试次数（0=关闭）
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def parse_schedule(spec: str) -> tuple[str, Any]:
    """解析调度写法 → ("weekly", (dow, hour, minute)) 或 ("interval", seconds)。

    兼容空格与 ``@`` 紧凑两种写法：``fri 17:30`` / ``fri@17:30`` / ``every 3600`` / ``every@3600``。
    非法写法抛 ValueError（由调用方转成用户可读提示）。
    """
    text = spec.strip()
    if "@" in text:
        text = text.replace("@", " ", 1)
    low = text.lower()

    if low.startswith("every "):
        seconds = float(low[len("every "):].rstrip("s"))
        if seconds <= 0:
            raise ValueError(f"间隔必须为正数: {spec!r}")
        return ("interval", seconds)

    parts = low.split()
    if len(parts) == 2:
        dow = _DOW_NAMES.get(parts[0])
        hhmm = parts[1].split(":")
        if dow is not None and len(hhmm) == 2:
            hour, minute = int(hhmm[0]), int(hhmm[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return ("weekly", (dow, hour, minute))
    raise ValueError(
        f"无法解析调度: {spec!r}（支持 'fri 17:30' / 'fri@17:30' / 'every 3600'）"
    )


class RoutineManager:
    """例行任务注册表：add/remove/list + 持久化 + 应用到调度器。"""

    def __init__(self, memory: Any, data_root: Path | None = None) -> None:
        self.memory = memory
        # 默认落在 data/routines.json（与 sessions.db 同目录），vault 之外不污染记忆
        self._path = data_root or (memory.vault_path.parent.parent / "routines.json")
        self._routines: dict[str, Routine] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, list):
            return
        for item in data:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            self._routines[str(item["name"])] = Routine(
                name=str(item["name"]),
                message=str(item.get("message", "")),
                schedule=str(item.get("schedule", "")),
                agent=str(item.get("agent", "housekeeper")),
                archive_to_wiki=bool(item.get("archive_to_wiki", False)),
                retries=int(item.get("retries", 0) or 0),
                created=str(item.get("created", "")),
            )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "name": r.name, "message": r.message, "schedule": r.schedule,
                "agent": r.agent, "archive_to_wiki": r.archive_to_wiki,
                "retries": r.retries, "created": r.created,
            }
            for r in self._routines.values()
        ]
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # 增删查
    # ------------------------------------------------------------------ #
    def add(
        self,
        name: str,
        message: str,
        schedule: str,
        *,
        agent: str = "housekeeper",
        archive_to_wiki: bool = False,
        retries: int = 0,
    ) -> Routine:
        """注册一项例行任务（同名覆盖）。调度写法非法抛 ValueError。

        ``retries`` 为瞬态失败重试次数（0=关闭，默认）；负值按 0 处理。
        """
        name = name.strip()
        if not name or not message.strip():
            raise ValueError("name 与 message 不能为空")
        parse_schedule(schedule)  # 先校验，非法不落盘
        routine = Routine(
            name=name, message=message.strip(), schedule=schedule,
            agent=agent or "housekeeper", archive_to_wiki=archive_to_wiki,
            retries=max(int(retries), 0),
        )
        self._routines[name] = routine
        self._save()
        return routine

    def remove(self, name: str) -> bool:
        """移除例行任务；返回是否实际移除。"""
        if name not in self._routines:
            return False
        del self._routines[name]
        self._save()
        return True

    def get(self, name: str) -> Routine | None:
        return self._routines.get(name)

    def list(self) -> list[Routine]:
        return sorted(self._routines.values(), key=lambda r: r.name)

    # ------------------------------------------------------------------ #
    # 应用：注册进调度器（幂等，同名覆盖）
    # ------------------------------------------------------------------ #
    def apply_to(self, scheduler: ProactiveScheduler) -> int:
        """把全部例行任务注册进调度器并确保调度线程已启动；返回注册数。"""
        count = 0
        for r in self._routines.values():
            kind, parsed = parse_schedule(r.schedule)
            if kind == "weekly":
                dow, hour, minute = parsed
                scheduler.add_weekly_job(
                    r.name, r.message, dow, hour, minute, agent_name=r.agent,
                    retries=r.retries,
                )
            else:
                scheduler.add_interval_job(
                    r.name, r.message, interval_seconds=parsed, agent_name=r.agent,
                    retries=r.retries,
                )
            count += 1
        if count:
            scheduler.start()
        return count

    # ------------------------------------------------------------------ #
    # 下次触发时间 + 重试状态（供面板展示）
    # ------------------------------------------------------------------ #
    def next_run(self, scheduler: ProactiveScheduler, name: str) -> str:
        from datetime import UTC

        try:
            job = next(j for j in scheduler.list_jobs() if j.name == name)
        except StopIteration:
            return "—"
        if job.next_run is None:
            return "—"
        # 展示用本地时间（去掉 UTC 后缀便于阅读）
        local = job.next_run.astimezone().strftime("%m-%d %H:%M")
        return local or "—"

    def retry_state(self, scheduler: ProactiveScheduler, name: str) -> dict[str, Any]:
        """读取调度器任务的重试状态：``retries`` 配额与 ``fail_count`` 已用次数。

        供面板显示 ``🔁 retry #fail_count/retries``；任务不在调度器返回全 0。
        """
        try:
            job = next(j for j in scheduler.list_jobs() if j.name == name)
        except StopIteration:
            return {"retries": 0, "fail_count": 0}
        return {
            "retries": int(getattr(job, "retries", 0)),
            "fail_count": int(getattr(job, "fail_count", 0)),
        }
