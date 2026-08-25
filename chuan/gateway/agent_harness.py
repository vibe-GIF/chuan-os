"""Agent Harness —— 后台委派外部 agent 干活（fire-and-forget）+ 任务状态机。

借鉴 deepseek-harness：父 Agent 把编码任务整体委派给原生 CLI 子进程
（claude_code / opencode / pi / prime_agent），黑盒跑完只回收最终结果，
不干涉其内部 tool call 循环。

任务状态机（借鉴 dsh-agent-teams + NVIDIA AVO 的 idle/ready/running/done）：
    pending → ready → running → done / failed
- pending：已登记，等待依赖（depends_on）全部结束
- ready ：依赖已结束，可被认领执行
- running：已被认领，正在执行（只执行一次，防止两个执行器干同一个活）
- done / failed：终态
- 依赖 DAG：submit 带 depends_on=[task_id...]，依赖未全结束则进 pending，
  每结束一个任务自动推进所有依赖就绪的 pending 任务。
- 环不可达：submit 要求 depends_on 引用已存在的任务（否则抛 ValueError），
  而新任务的 id 在登记后才生成，故无法形成「先建依赖后建」的环，天然防死锁。
- 依赖失败不阻断下游：depends_on 只要全部「结束」（done 或 failed）就推进，
  与 dsh-agent-teams 一致（多数下游任务不强制依赖成功）。

chuan 的适配（单进程 asyncio）：
- submit() 派发即返（返回 task_id），不阻塞主对话
- 任务在 supervisor 常驻事件循环里后台执行（CommandAgent 已用
  asyncio.to_thread，不会阻塞事件循环）
- 完成/失败通过 on_done 回调异步回推（HUD / CLI / 微信等旁路通道）
- snapshot() 提供任务状态快照，供 TUI 班底看板

线程模型：
- submit 时若给 loop，用 run_coroutine_threadsafe 调度到该事件循环；
  否则（测试/独立使用）自己起守护线程 + asyncio.run。
- 完成回调在任务所在的事件循环线程触发，应保持轻量（打印/发命令），
  不得阻塞（否则会卡住 chuan-event-loop）。
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from chuan.agents.base import AgentResult
from chuan.agent_pool import AgentPool

# 完成回调签名: (info: dict) -> None，info 字段见 snapshot 条目
DoneCallback = Callable[[dict[str, Any]], None]

# 终态：依赖检查只认这两个状态
_TERMINAL = ("done", "failed")
# 活跃态（未结束）
_ACTIVE = ("pending", "ready", "running")

# 最大保留的已完成任务数（防内存无限增长）
_MAX_KEEP_DONE = 200


class AgentHarness:
    """后台委派任务管理器 + 依赖感知的任务状态机。"""

    def __init__(self, pool: AgentPool, bus: Any = None) -> None:
        self._pool = pool
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._seq = itertools.count(1)
        self._done_cbs: list[DoneCallback] = []
        # N45 事件总线：任务生命周期事件（submitted/started/done/failed），
        # 全旁路——未启用/无 Redis/异常都不阻断任务主流程
        if bus is None:
            from chuan.bus import get_bus

            bus = get_bus()
        self._bus = bus

    # ------------------------------------------------------------------ #
    # 事件发布（旁路）
    # ------------------------------------------------------------------ #
    def _publish(self, type_: str, task_id: str, entry: dict[str, Any], **extra: Any) -> None:
        if self._bus is None:
            return
        try:
            from chuan.bus import make_event

            payload = {
                "task_id": task_id,
                "agent": entry.get("agent"),
                "status": entry.get("status"),
                "session_id": entry.get("session_id"),
                "mission": entry.get("mission", ""),
                **extra,
            }
            self._bus.publish(
                "agent.task",
                make_event(
                    f"delegate.{type_}",
                    f"harness:{entry.get('agent', '')}",
                    payload,
                ),
            )
        except Exception:  # noqa: BLE001, S110 - 事件发布是旁路
            pass

    # ------------------------------------------------------------------ #
    # 回调注册
    # ------------------------------------------------------------------ #
    def on_done(self, cb: DoneCallback) -> None:
        """注册全局完成回调（对后续所有 delegate 任务生效）。"""
        with self._lock:
            self._done_cbs.append(cb)

    # ------------------------------------------------------------------ #
    # 委派
    # ------------------------------------------------------------------ #
    def submit(
        self,
        agent_name: str,
        task: str,
        *,
        session_id: str = "default",
        loop: asyncio.AbstractEventLoop | None = None,
        on_done: DoneCallback | None = None,
        depends_on: list[str] | None = None,
        mission: str = "",
    ) -> str:
        """登记并派发一个后台任务，立即返回 task_id。

        Args:
            agent_name: 常驻池里的外部 agent 名（claude_code / opencode / pi / prime_agent）
            task: 交给 agent 的任务文本
            session_id: 会话 ID（用于线程隔离）
            loop: 目标事件循环；None 时自起守护线程跑（测试用）
            on_done: 本次任务独有的完成回调（与全局回调叠加）
            depends_on: 前置任务 task_id 列表；全部结束后本任务才运行。
                省略/空 = 立即运行。引用不存在的任务会抛 ValueError。
            mission: 关联的 Mission 名（N32，跨对话长任务；完成时自动回写进度）

        Returns:
            新任务的 task_id

        Raises:
            ValueError: depends_on 引用不存在的任务
        """
        if depends_on:
            bad = [d for d in depends_on if d not in self._tasks]
            if bad:
                raise ValueError(f"depends_on 引用不存在的任务：{bad}")
        task_id = f"delegate-{next(self._seq)}"

        deps = list(depends_on) if depends_on else []
        # 依赖已全部结束 → 直接 ready；否则 pending 等待依赖
        ready = all(self._tasks.get(d, {}).get("status") in _TERMINAL for d in deps)
        entry: dict[str, Any] = {
            "task_id": task_id,
            "agent": agent_name,
            "task": task,
            "depends_on": deps,
            "status": "ready" if ready else "pending",
            "success": None,
            "result": "",
            "claimed_by": None,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "finished_at": None,
            "loop": loop,
            "session_id": session_id,
            "on_done": on_done,
            "mission": mission,
        }
        with self._lock:
            self._tasks[task_id] = entry
        self._publish("submitted", task_id, entry)

        if ready:
            self._schedule(task_id)
        return task_id

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def get(self, task_id: str) -> dict[str, Any] | None:
        """取单个任务快照；不存在返回 None。"""
        with self._lock:
            entry = self._tasks.get(task_id)
            return dict(entry) if entry else None

    def snapshot(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """任务状态快照（TUI 看板数据源）。

        Args:
            status: 过滤 "pending" / "ready" / "running" / "done" / "failed"；
                None 返回全部
        """
        with self._lock:
            items = [dict(e) for e in self._tasks.values()]
        if status:
            items = [e for e in items if e["status"] == status]
        return items

    # ------------------------------------------------------------------ #
    # 调度
    # ------------------------------------------------------------------ #
    def _schedule(self, task_id: str) -> None:
        """原子认领（ready → running）并启动执行。只执行一次。"""
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None or entry["status"] != "ready":
                return  # 已被其它调度器认领 / 不存在
            entry["status"] = "running"
            entry["claimed_by"] = "harness"
            loop = entry.get("loop")
        self._publish("started", task_id, entry)

        coro = self._run(task_id)
        if loop is not None:
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            threading.Thread(
                target=lambda: asyncio.run(coro),
                name=f"chuan-harness-{task_id}",
                daemon=True,
            ).start()

    def _dep_ok(self, d: str) -> bool:
        """依赖是否已结束。已被裁剪的终态任务视作已结束（submit 保证存在过）。"""
        e = self._tasks.get(d)
        if e is None:
            return True
        return e.get("status") in _TERMINAL

    def _promote_pending(self) -> None:
        """任务结束后推进依赖就绪的 pending 任务 → ready 并调度。"""
        with self._lock:
            to_schedule = [
                tid
                for tid, e in self._tasks.items()
                if e["status"] == "pending"
                and all(self._dep_ok(d) for d in (e.get("depends_on") or []))
            ]
            for tid in to_schedule:
                self._tasks[tid]["status"] = "ready"
        for tid in to_schedule:
            self._schedule(tid)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _run(self, task_id: str) -> None:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            agent_name = entry["agent"]
            task = entry["task"]
            session_id = entry["session_id"]

        agent = self._pool.get(agent_name)
        if agent is None:
            result = AgentResult(
                content=f"[HARNESS ERROR] 未知 agent：{agent_name}（可用："
                f"{'、'.join(self._pool.list_resident()) or '无'}）",
                agent_name=agent_name,
                success=False,
            )
        else:
            try:
                result = await agent.run(
                    task,
                    context={"thread_id": f"{session_id}:bg:{task_id}"},
                )
            except Exception as exc:  # noqa: BLE001 - 委派失败也要正常回推
                result = AgentResult(
                    content=f"[HARNESS ERROR] {exc}",
                    agent_name=agent_name,
                    success=False,
                )
        self._mark_done(task_id, result)

    def _mark_done(self, task_id: str, result: AgentResult) -> None:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            entry["status"] = "done" if result.success else "failed"
            entry["success"] = result.success
            entry["result"] = result.content
            entry["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            info = dict(entry)
            cbs = list(self._done_cbs)
            on_done = entry.get("on_done")
            if on_done is not None:
                cbs.append(on_done)
            # 内存上限：完成态任务只保留最近 _MAX_KEEP_DONE 条
            done_ids = [
                e["task_id"]
                for e in self._tasks.values()
                if e["status"] in _TERMINAL
            ]
            for old_id in done_ids[: max(len(done_ids) - _MAX_KEEP_DONE, 0)]:
                self._tasks.pop(old_id, None)
        self._publish(
            "done" if result.success else "failed",
            task_id,
            entry,
            success=bool(result.success),
        )

        for cb in cbs:  # 在事件循环线程触发；回调应轻量
            try:
                cb(info)
            except Exception:  # noqa: BLE001, S110 - 回推失败是旁路
                pass

        self._promote_pending()
