"""N32 Mission 长任务追踪 —— 跨对话的长任务看板（借鉴 Aivy）。

N21 ``AgentHarness`` 的任务状态机只管「单次会话内」的后台委派（内存态，
重启即失）。Mission 把长任务做成一等概念：**跨会话的目标 + 状态机 + 关联的
后台任务**，持久化到 ``data/missions.json``（磁盘真相，重启不丢），供
``/mission`` 看板管理。

生命周期：``active``（进行中）→ ``paused``（暂停）/ ``done``（完成）/
``failed``（失败）
- ``start`` 登记长目标；``delegate(..., mission=<name>)`` 把后台任务挂到
  mission 上（harness 任务完成后自动 ``update`` 进度 + 关联 task_id）
- ``finish`` / ``pause`` / ``resume`` / ``remove`` 手动管理

与 routines（N28）分工：routine 是「周期性自转」；mission 是「一次长目标、
跨会话推进」的看板。二者都持久化 JSON、磁盘真相、确定性可测。

设计约束（与项目一致）：纯本地、确定性；异常一律旁路不阻断主流程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 合法状态集合
_MISSION_STATUS = ("active", "paused", "done", "failed")


@dataclass
class Mission:
    """一项跨会话的长任务目标。"""

    name: str
    goal: str
    agent: str = "housekeeper"
    status: str = "active"  # active / paused / done / failed
    progress: str = ""      # 最近进度摘要（后台任务结果等）
    task_ids: list[str] = field(default_factory=list)  # 关联的后台任务
    source: str = ""
    created: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    updated: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )


class MissionManager:
    """Mission 注册表：start/get/list/update/finish/pause/resume/remove + 持久化。"""

    def __init__(self, memory: Any, data_root: Path | None = None) -> None:
        self.memory = memory
        # 默认落在 data/missions.json（与 sessions.db 同目录），vault 之外不污染记忆
        self._path = data_root or (memory.vault_path.parent.parent / "missions.json")
        self._missions: dict[str, Mission] = {}
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
            self._missions[str(item["name"])] = Mission(
                name=str(item["name"]),
                goal=str(item.get("goal", "")),
                agent=str(item.get("agent", "housekeeper")),
                status=str(item.get("status", "active")),
                progress=str(item.get("progress", "")),
                task_ids=[str(t) for t in (item.get("task_ids") or [])],
                source=str(item.get("source", "")),
                created=str(item.get("created", "")),
                updated=str(item.get("updated", "")),
            )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "name": m.name, "goal": m.goal, "agent": m.agent,
                "status": m.status, "progress": m.progress,
                "task_ids": m.task_ids, "source": m.source,
                "created": m.created, "updated": m.updated,
            }
            for m in self._missions.values()
        ]
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # 增删查改
    # ------------------------------------------------------------------ #
    def start(
        self,
        name: str,
        goal: str,
        *,
        agent: str = "housekeeper",
        source: str = "",
    ) -> Mission:
        """登记一项长任务目标（同名覆盖）。name 与 goal 不能为空。"""
        name = name.strip()
        if not name or not goal.strip():
            raise ValueError("name 与 goal 不能为空")
        mission = Mission(
            name=name, goal=goal.strip(),
            agent=agent or "housekeeper", source=source,
        )
        self._missions[name] = mission
        self._save()
        return mission

    def get(self, name: str) -> Mission | None:
        return self._missions.get(name)

    def list(self, status: str | None = None) -> list[Mission]:
        missions = sorted(self._missions.values(), key=lambda m: m.name)
        if status:
            missions = [m for m in missions if m.status == status]
        return missions

    def update(
        self,
        name: str,
        *,
        progress: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
    ) -> bool:
        """更新一条 mission（进度/状态/关联任务）；不存在返回 False。"""
        m = self._missions.get(name)
        if m is None:
            return False
        if task_id and task_id not in m.task_ids:
            m.task_ids.append(task_id)
        if progress is not None:
            m.progress = str(progress)
        if status is not None and status in _MISSION_STATUS:
            m.status = status
        m.updated = datetime.now().isoformat(timespec="seconds")
        self._save()
        return True

    def finish(self, name: str, summary: str = "", *, success: bool = True) -> bool:
        """标记完成/失败并记录结果摘要。"""
        return self.update(
            name, progress=summary or None,
            status="done" if success else "failed",
        )

    def pause(self, name: str) -> bool:
        return self.update(name, status="paused")

    def resume(self, name: str) -> bool:
        return self.update(name, status="active")

    def remove(self, name: str) -> bool:
        if name not in self._missions:
            return False
        del self._missions[name]
        self._save()
        return True
