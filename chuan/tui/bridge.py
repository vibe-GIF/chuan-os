"""N17 TUI 桥 —— Textual 事件循环 ↔ RuntimeSupervisor 常驻事件循环。

RuntimeSupervisor 自带一个常驻 asyncio 线程（MCP / aiosqlite 都绑在它上面），
它的 ``dispatch()`` 是同步阻塞接口（内部 run_coroutine_threadsafe + future.result）。
TUI 是另一个事件循环（Textual），因此：

- 唤醒（wake_up，含 MCP 连接/记忆索引）放后台线程，不卡首帧
- dispatch 通过 ``asyncio.to_thread`` 扔进线程池，TUI 循环保持可响应
- 岗位进度事件（on_progress）与主动提醒经线程安全 queue 传给 TUI 轮询渲染
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any


def _default_factory(**kwargs: Any) -> Any:
    from chuan.runtime_supervisor import RuntimeSupervisor

    return RuntimeSupervisor(**kwargs)


class SupervisorBridge:
    """TUI 与 RuntimeSupervisor 之间的全部通信都走这个对象。"""

    def __init__(self, supervisor_factory: Callable[..., Any] | None = None) -> None:
        self._factory = supervisor_factory or _default_factory
        self._supervisor: Any = None
        self._ready = False
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._events: queue.Queue = queue.Queue()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """后台线程里唤醒幕僚长（MCP 连接等耗时步骤不阻塞 TUI 首帧）。"""
        if self._thread is not None:
            return

        def _run() -> None:
            try:
                sup = self._factory(
                    on_progress=self._events.put,
                    on_proactive_alert=self._on_alert,
                )
                # 后台委派（fire-and-forget）完成 → 塞进 TUI 事件队列，由轮询渲染
                harness = getattr(sup, "agent_harness", None)
                if harness is not None:
                    harness.on_done(self._on_delegate_done)
                sup.wake_up()
                self._supervisor = sup
                self._ready = True
            except Exception as exc:  # noqa: BLE001 - 启动失败要展示给用户
                self._error = str(exc)

        self._thread = threading.Thread(target=_run, name="chuan-tui-bridge", daemon=True)
        self._thread.start()

    def _on_alert(self, alert: Any) -> None:
        self._events.put({
            "event": "alert",
            "job": getattr(alert, "job_name", ""),
            "content": getattr(alert, "content", str(alert)),
            "error": bool(getattr(alert, "error", False)),
        })

    def _on_delegate_done(self, info: dict[str, Any]) -> None:
        """后台委派完成事件（在 supervisor 事件循环线程触发，仅入队）。"""
        self._events.put({"event": "delegate_done", **info})

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str | None:
        return self._error

    def wait_ready(self, timeout: float = 60.0) -> bool:
        """阻塞等待唤醒完成（供测试）。"""
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        return self._ready

    def shutdown(self) -> None:
        if self._supervisor is not None:
            try:
                self._supervisor.shutdown()
            except Exception:  # noqa: BLE001 - 退出路径尽力清理
                pass

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def _require(self) -> Any:
        if not self._ready or self._supervisor is None:
            raise RuntimeError("幕僚长尚未就绪")
        return self._supervisor

    def route_preview(self, message: str) -> str | None:
        """关键词路由预览（纯本地，无 LLM，可在发送前立刻显示）。"""
        if not self._ready:
            return None
        try:
            return self._supervisor.route_preview(message)
        except Exception:  # noqa: BLE001 - 预览失败不影响主流程
            return None

    def workers(self) -> list[tuple[str, str]]:
        """返回 [(persona名, 显示名), …]。"""
        if not self._ready:
            return []
        out: list[tuple[str, str]] = []
        try:
            for name in self._supervisor.list_workers():
                role = self._supervisor.get_worker(name)
                out.append((name, getattr(role, "display_name", name) or name))
        except Exception:  # noqa: BLE001
            return []
        return out

    def brain_name(self) -> str:
        if not self._ready:
            return ""
        try:
            brain = self._supervisor.brains.default()
            return getattr(brain, "name", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    def memory_note_count(self) -> int:
        """长期笔记条数（notes/ 命名空间下的 Markdown 数）。"""
        if not self._ready:
            return 0
        try:
            memory = getattr(self._supervisor, "memory", None)
            notes = memory.vault_path / "notes"
            return sum(1 for _ in notes.rglob("*.md"))
        except Exception:  # noqa: BLE001 - 统计失败按 0 展示
            return 0

    def consolidation_status(self) -> str | None:
        """巩固 worker 的最近状态（None=运行中/未完成）。"""
        if not self._ready:
            return None
        return getattr(self._supervisor, "consolidation_status", None)

    def delegate(self, agent_name: str, task: str, mission: str = "") -> str:
        """后台委派（派发即返 task_id，不阻塞 TUI 循环）。"""
        sup = self._require()
        return sup.delegate(agent_name, task, mission=mission)

    def delegate_snapshot(self) -> list[dict[str, Any]]:
        """后台任务状态快照；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "delegate_snapshot", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 看板失败不影响主界面
            return []

    def mcp_status(self) -> list[dict[str, Any]]:
        """MCP server 状态快照；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "mcp_status", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return []

    def monitor_status(self) -> dict[str, Any]:
        """P1 监督者快照（轨迹/死胡同/redirect）；未就绪返回空统计。"""
        if not self._ready:
            return {"traces": [], "dead_ends": [], "redirects": [],
                    "stats": {"traces": 0, "active": 0, "dead_ends": 0,
                              "redirects": 0}}
        fn = getattr(self._supervisor, "monitor_status", None)
        if fn is None:
            return {"traces": [], "dead_ends": [], "redirects": [],
                    "stats": {"traces": 0, "active": 0, "dead_ends": 0,
                              "redirects": 0}}
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return {"traces": [], "dead_ends": [], "redirects": [],
                    "stats": {"traces": 0, "active": 0, "dead_ends": 0,
                              "redirects": 0}}

    def mcp_connect(self, name: str) -> bool:
        """运行时连接单个 MCP server。"""
        if not self._ready:
            return False
        fn = getattr(self._supervisor, "mcp_connect", None)
        if fn is None:
            return False
        try:
            return bool(fn(name))
        except Exception:  # noqa: BLE001
            return False

    def mcp_disconnect(self, name: str) -> bool:
        """运行时断开单个 MCP server。"""
        if not self._ready:
            return False
        fn = getattr(self._supervisor, "mcp_disconnect", None)
        if fn is None:
            return False
        try:
            return bool(fn(name))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    # N27 知识原子自动沉淀 —— 待人工确认队列（/howto 数据源）
    # ------------------------------------------------------------------ #
    def howto_staging(self) -> list[dict[str, Any]]:
        """待人工确认的知识原子候选；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "howto_staging", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return []

    def howto_show(self, name: str) -> str:
        """查看单个待确认候选的完整内容。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "howto_show", None)
        if fn is None:
            return "（当前幕僚长不支持知识原子队列）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（读取失败：{exc}）"

    def howto_approve(self, name: str, rename: str | None = None) -> str:
        """人工确认：候选落入 howto 知识库。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "howto_approve", None)
        if fn is None:
            return "（当前幕僚长不支持知识原子队列）"
        try:
            return fn(name, rename)
        except Exception as exc:  # noqa: BLE001
            return f"（确认失败：{exc}）"

    def howto_discard(self, name: str) -> str:
        """人工否决：丢弃候选，不入库。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "howto_discard", None)
        if fn is None:
            return "（当前幕僚长不支持知识原子队列）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（丢弃失败：{exc}）"

    # ------------------------------------------------------------------ #
    # N30 自动技能创建（/skill 数据源）
    # ------------------------------------------------------------------ #
    def skill_staging(self) -> list[dict[str, Any]]:
        """待人工确认的技能候选列表；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "skill_staging", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return []

    def skill_status(self) -> dict[str, Any]:
        """技能看板统计（已注册 prompt 技能数 + 待确认数）。"""
        if not self._ready:
            return {"registered": 0, "pending": 0}
        fn = getattr(self._supervisor, "skill_status", None)
        if fn is None:
            return {"registered": 0, "pending": 0}
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return {"registered": 0, "pending": 0}

    def skill_show(self, name: str) -> str:
        """查看单个待确认技能候选的完整内容。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "skill_show", None)
        if fn is None:
            return "（当前幕僚长不支持技能队列）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（读取失败：{exc}）"

    def skill_approve(self, name: str, rename: str | None = None) -> str:
        """人工确认：候选写成 skills/*.yaml 并运行时注册。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "skill_approve", None)
        if fn is None:
            return "（当前幕僚长不支持技能队列）"
        try:
            return fn(name, rename)
        except Exception as exc:  # noqa: BLE001
            return f"（确认失败：{exc}）"

    def skill_discard(self, name: str) -> str:
        """人工否决：丢弃候选技能，不注册。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "skill_discard", None)
        if fn is None:
            return "（当前幕僚长不支持技能队列）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（丢弃失败：{exc}）"

    # ------------------------------------------------------------------ #
    # N32 Mission 长任务追踪（/mission 数据源）
    # ------------------------------------------------------------------ #
    def mission_list(self) -> list[dict[str, Any]]:
        """跨对话长任务看板清单；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "mission_list", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return []

    def mission_start(self, name: str, goal: str) -> str:
        """登记一项跨对话长任务目标。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "mission_start", None)
        if fn is None:
            return "（当前幕僚长不支持 Mission 看板）"
        try:
            return fn(name, goal)
        except Exception as exc:  # noqa: BLE001
            return f"（登记失败：{exc}）"

    def mission_finish(self, name: str, summary: str = "") -> str:
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "mission_finish", None)
        if fn is None:
            return "（当前幕僚长不支持 Mission 看板）"
        try:
            return fn(name, summary)
        except Exception as exc:  # noqa: BLE001
            return f"（更新失败：{exc}）"

    def mission_pause(self, name: str) -> str:
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "mission_pause", None)
        if fn is None:
            return "（当前幕僚长不支持 Mission 看板）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（更新失败：{exc}）"

    def mission_resume(self, name: str) -> str:
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "mission_resume", None)
        if fn is None:
            return "（当前幕僚长不支持 Mission 看板）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（更新失败：{exc}）"

    def mission_remove(self, name: str) -> str:
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "mission_remove", None)
        if fn is None:
            return "（当前幕僚长不支持 Mission 看板）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（删除失败：{exc}）"

    # ------------------------------------------------------------------ #
    # N33 ACI 预判注入（/aci 数据源）
    # ------------------------------------------------------------------ #
    def aci_status(self) -> dict[str, Any]:
        """ACI 预判注入面板数据；未就绪/不支持返回空态。"""
        if not self._ready:
            return {"memory": 0, "wiki": 0, "total": 0, "injected": False}
        fn = getattr(self._supervisor, "aci_status", None)
        if fn is None:
            return {"memory": 0, "wiki": 0, "total": 0, "injected": False}
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return {"memory": 0, "wiki": 0, "total": 0, "injected": False}

    # ------------------------------------------------------------------ #
    # N35 断点续跑（/resume 数据源）
    # ------------------------------------------------------------------ #
    def resume_list(self) -> list[dict[str, Any]]:
        """可恢复的断点档案清单；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "resume_list", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return []

    def resume_to(self, worker_name: str, session_id: str) -> str:
        """按岗位断点续跑（复用已完成子任务，只跑未完成）。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "resume_to", None)
        if fn is None:
            return "（当前幕僚长不支持断点续跑）"
        try:
            return fn(worker_name, session_id)
        except Exception as exc:  # noqa: BLE001
            return f"（续跑失败：{exc}）"

    def resume_clear(self, session_id: str) -> str:
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "resume_clear", None)
        if fn is None:
            return "（当前幕僚长不支持断点续跑）"
        try:
            return fn(session_id)
        except Exception as exc:  # noqa: BLE001
            return f"（清除失败：{exc}）"

    # ------------------------------------------------------------------ #
    # N28 例行自动化闭环（/routine 数据源）
    # ------------------------------------------------------------------ #
    def routine_list(self) -> list[dict[str, Any]]:
        """例行任务清单（含下次触发时间）；未就绪/不支持返回空表。"""
        if not self._ready:
            return []
        fn = getattr(self._supervisor, "routine_list", None)
        if fn is None:
            return []
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 面板数据失败不影响主界面
            return []

    def routine_add(
        self,
        name: str,
        message: str,
        schedule: str,
        archive_to_wiki: bool = False,
        retries: int = 0,
    ) -> str:
        """注册一项例行任务并应用到调度器。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "routine_add", None)
        if fn is None:
            return "（当前幕僚长不支持例行任务）"
        try:
            return fn(
                name, message, schedule,
                archive_to_wiki=archive_to_wiki, retries=retries,
            )
        except Exception as exc:  # noqa: BLE001
            return f"（添加失败：{exc}）"

    def routine_remove(self, name: str) -> str:
        """移除例行任务并取消调度。"""
        if not self._ready:
            return "（幕僚长尚未就绪）"
        fn = getattr(self._supervisor, "routine_remove", None)
        if fn is None:
            return "（当前幕僚长不支持例行任务）"
        try:
            return fn(name)
        except Exception as exc:  # noqa: BLE001
            return f"（移除失败：{exc}）"

    def drain_events(self) -> list[dict[str, Any]]:
        """取走所有排队事件（进度事件 + 主动提醒）。"""
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #
    async def send(self, message: str) -> dict[str, Any]:
        """自动路由发送（阻塞调用放线程池，TUI 循环不卡）。"""
        sup = self._require()
        return await asyncio.to_thread(sup.dispatch, message)

    async def send_to(self, worker_name: str, message: str) -> dict[str, Any]:
        """锁定角色直发。"""
        sup = self._require()
        return await asyncio.to_thread(sup.dispatch_to, worker_name, message)
