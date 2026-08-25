"""团队状态落盘 —— 岗位任务清单的磁盘真相（借鉴 dsh-agent-teams team.json）。

dsh 是多进程 sub-agent 架构必须文件化；chuan 是单进程 asyncio，
落盘目的不同：
1. CLI/语音重启后能报告「上次还有 N 个子任务未完成」（冷恢复提示）
2. 归档保留完整任务历史（复盘/审计）

写入时机：dispatch 规划后首次写，每个子任务状态变更时更新；
全部落定（done/failed）后标记 status=finished 保留归档，不删除。

文件布局（简化版，无邮箱）:
    data/teams/<session_id>.json
    {"role": "研究", "task": "...", "status": "running|finished",
     "updated_at": "...", "subtasks": [{"id": "s1", "description": "...",
        "status": "pending|running|done|failed", "attempts": 2,
        "summary": "结果摘要（前 200 字）"}]}
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# session_id 直接拼文件名有路径注入风险（"../../x"），白名单清洗
_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-:]")


def _teams_dir(root: Path | str | None = None) -> Path:
    if root is not None:
        base = Path(root)
    else:
        base = Path(__file__).resolve().parent.parent / "data"
    d = base / "teams"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(session_id: str) -> str:
    cleaned = _SAFE_ID.sub("_", session_id)[:80] or "default"
    return f"{cleaned}.json"


class TeamStateWriter:
    """岗位任务的磁盘真相写入器。写失败静默（落盘是旁路，不能阻断执行）。"""

    def __init__(self, role: str, task: str, session_id: str,
                 root: Path | str | None = None) -> None:
        self._path = _teams_dir(root) / _safe_name(session_id)
        self._doc: dict[str, Any] = {
            "role": role,
            "task": task,
            "status": "running",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "subtasks": [],
        }

    def init_subtasks(self, subtasks: list[dict[str, Any]]) -> None:
        """规划完成后初始化子任务清单（全 pending）。"""
        self._doc["subtasks"] = [
            {"id": st["id"], "description": st["description"],
             "status": "pending", "attempts": 1, "summary": ""}
            for st in subtasks
        ]
        self._flush()

    def update(self, subtask_id: str, status: str, attempts: int = 1,
               summary: str = "") -> None:
        """更新单个子任务状态。未知 id 忽略。"""
        for st in self._doc["subtasks"]:
            if st["id"] == subtask_id:
                st["status"] = status
                st["attempts"] = attempts
                st["summary"] = summary[:200]
                break
        self._flush()

    def finish(self) -> None:
        """全部子任务落定后标记整体状态。"""
        self._doc["status"] = "finished"
        self._flush()

    def _flush(self) -> None:
        self._doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._path.write_text(
                json.dumps(self._doc, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # 磁盘满/权限等，落盘失败不阻断执行


def load_unfinished(root: Path | str | None = None) -> list[dict[str, Any]]:
    """扫描团队状态目录，返回未完成（status=running）的任务记录。

    supervisor 重启时调用，报告「上次有 N 个任务未完成」。
    """
    try:
        files = sorted(_teams_dir(root).glob("*.json"))
    except OSError:
        return []
    unfinished: list[dict[str, Any]] = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("status") == "running":
            unfinished.append(doc)
    return unfinished
