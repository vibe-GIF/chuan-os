"""任务断点续跑 —— 子任务级结果缓存（借鉴 Aivy「流式打断不丢工具」）。

背景：长任务（多子任务、多波并行）执行到一半被打断（语音开口 / Esc 软中断 /
进程重启）时，已完成的子任务结果不应丢弃——下次对同一任务继续时只重跑
未完成部分。

设计：
- `data/task_resume/<session_id>.json` 磁盘真相：一个 session 一份档案，
  存「规划 plan + 每个子任务的完成结果」。
- `RoleTaskResumeStore.save_plan` 规划落定即写；`save_result` 每个子任务
  完成即写（旁路，异常吞掉）。
- `resume_plan(session_id)` 读回上次 plan 与已完成结果，供
  `PersonaRole.resume()` 复用已完成子任务、只跑未完成部分。

幂等与安全：
- session_id 白名单清洗（复用 team_state 的清洗规则，防路径注入）。
- 全程旁路 try/except：缓存写失败绝不影响任务执行。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# session_id 直接拼文件名有路径注入风险（"../../x"），白名单清洗
_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-:]")

# 单条子任务结果保留长度上限（防档案无限膨胀）
_MAX_RESULT_CHARS = 4000


def _resume_dir(root: Path | str | None = None) -> Path:
    if root is not None:
        base = Path(root)
    else:
        base = Path(__file__).resolve().parent.parent.parent / "data"
    d = base / "task_resume"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(session_id: str) -> str:
    cleaned = _SAFE_ID.sub("_", session_id)[:80] or "default"
    return f"{cleaned}.json"


class RoleTaskResumeStore:
    """岗位任务断点档案：plan + 子任务结果，按 session 持久化。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self._dir = _resume_dir(root)

    # ------------------------------------------------------------------ #
    # 写入（旁路，异常静默）
    # ------------------------------------------------------------------ #
    def save_plan(
        self,
        session_id: str,
        role: str,
        task: str,
        plan: list[dict[str, Any]],
    ) -> None:
        """规划落定即存：档案里保留 plan（供 resume 复用，不再重新规划）。

        ``plan`` 为子任务描述列表（含 id / description / agent / depends_on）。
        """
        try:
            doc = self._load(session_id)
            doc["role"] = role
            doc["task"] = task
            doc["plan"] = [
                {
                    "id": str(st.get("id") or f"s{i + 1}"),
                    "description": str(st.get("description", "")),
                    "agent": str(st.get("agent") or "auto"),
                    "depends_on": list(st.get("depends_on") or []),
                }
                for i, st in enumerate(plan)
            ]
            self._flush(session_id, doc)
        except Exception:  # noqa: BLE001 - 缓存失败不影响执行
            pass

    def save_result(
        self,
        session_id: str,
        subtask_id: str,
        *,
        success: bool,
        content: str,
        agent: str = "",
    ) -> None:
        """单个子任务完成即存结果（成功/失败都存，resume 时按需复用）。"""
        try:
            doc = self._load(session_id)
            doc.setdefault("results", {})[subtask_id] = {
                "success": bool(success),
                "content": str(content)[:_MAX_RESULT_CHARS],
                "agent": agent,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            self._flush(session_id, doc)
        except Exception:  # noqa: BLE001 - 缓存失败不影响执行
            pass

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def resume_plan(self, session_id: str) -> dict[str, Any] | None:
        """读回可恢复的 plan + 已完成结果；无档案/损坏返回 None。

        返回：``{"role", "task", "plan": [...], "results": {subtask_id: {...}}}``
        """
        try:
            doc = self._load(session_id)
        except Exception:  # noqa: BLE001 - 读取失败按无可恢复处理
            return None
        plan = doc.get("plan") or []
        if not plan:
            return None
        return {
            "role": doc.get("role", ""),
            "task": doc.get("task", ""),
            "plan": plan,
            "results": doc.get("results") or {},
        }

    def list_resumable(self) -> list[dict[str, Any]]:
        """列出所有存有 plan 的断点档案（供 /resume 面板）。"""
        out: list[dict[str, Any]] = []
        try:
            files = sorted(self._dir.glob("*.json"))
        except OSError:
            return []
        for f in files:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            plan = doc.get("plan") or []
            results = doc.get("results") or {}
            if not plan:
                continue
            done = sum(1 for p in plan if p.get("id") in results)
            out.append({
                "session_id": f.stem,
                "role": doc.get("role", ""),
                "task": str(doc.get("task", ""))[:60],
                "total": len(plan),
                "done": done,
                "updated_at": doc.get("updated_at", ""),
            })
        return sorted(out, key=lambda d: d["updated_at"], reverse=True)

    def clear(self, session_id: str) -> bool:
        """清除指定 session 的断点档案。"""
        path = self._dir / _safe_name(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load(self, session_id: str) -> dict[str, Any]:
        path = self._dir / _safe_name(session_id)
        if path.exists():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(doc, dict):
                    return doc
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _flush(self, session_id: str, doc: dict[str, Any]) -> None:
        doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path = self._dir / _safe_name(session_id)
        path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
