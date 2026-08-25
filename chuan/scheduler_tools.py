"""调度器 LangChain Tool 封装 —— 供 housekeeper 等角色动态管理定时任务。

三个工具共享一个延迟初始化的 ``ProactiveScheduler`` 单例：
- ``add_job_tool``：新增间隔任务
- ``remove_job_tool``：移除任务
- ``list_jobs_tool``：列出当前所有任务

主运行时可通过 ``set_scheduler()`` 注入已配置好 invoke_worker 的实例；
若未注入，``get_scheduler()`` 会用 no-op invoke_worker 创建默认实例。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from chuan.scheduler import ProactiveScheduler

_scheduler: ProactiveScheduler | None = None


def _default_invoke_worker(agent_name: str, message: str, session_id: str) -> dict[str, Any]:
    """no-op worker，工具单例在未被主运行时注入时使用。"""
    return {"messages": []}


def get_scheduler() -> ProactiveScheduler:
    """获取调度器单例；首次调用时延迟创建。"""
    global _scheduler
    if _scheduler is None:
        _scheduler = ProactiveScheduler(invoke_worker=_default_invoke_worker)
    return _scheduler


def set_scheduler(scheduler: ProactiveScheduler) -> None:
    """注入主运行时已配置的调度器实例（替换默认单例）。"""
    global _scheduler
    _scheduler = scheduler


def _add_job(name: str, message: str, interval_seconds: float, agent: str = "housekeeper") -> str:
    scheduler = get_scheduler()
    try:
        job = scheduler.add_interval_job(
            name=name,
            message=message,
            interval_seconds=interval_seconds,
            agent_name=agent,
        )
    except (ValueError, TypeError) as exc:
        return f"[ADD JOB FAILED] {exc}"
    return (
        f"已添加任务：{job.name}（agent={job.agent_name}，"
        f"间隔={job.interval_seconds}s，下次执行={job.next_run}）"
    )


def _remove_job(name: str) -> str:
    scheduler = get_scheduler()
    removed = scheduler.remove_job(name)
    return f"已移除任务：{name}" if removed else f"任务不存在：{name}"


def _list_jobs() -> str:
    scheduler = get_scheduler()
    jobs = scheduler.list_jobs()
    if not jobs:
        return "当前没有定时任务。"
    lines = ["当前定时任务："]
    for job in jobs:
        lines.append(
            f"- {job.name} | agent={job.agent_name} | "
            f"间隔={job.interval_seconds}s | 执行次数={job.run_count} | "
            f"下次执行={job.next_run}"
        )
    return "\n".join(lines)


add_job_tool: BaseTool = StructuredTool.from_function(
    func=_add_job,
    name="add_job",
    description="新增一项间隔定时任务。参数：name（任务名，唯一）、message（执行时发给 agent 的消息）、interval_seconds（间隔秒数，必须>0）、agent（执行角色，默认 housekeeper）。",
)

remove_job_tool: BaseTool = StructuredTool.from_function(
    func=_remove_job,
    name="remove_job",
    description="按任务名移除一项定时任务。参数：name（任务名）。",
)

list_jobs_tool: BaseTool = StructuredTool.from_function(
    func=_list_jobs,
    name="list_jobs",
    description="列出当前所有定时任务，包括任务名、agent、间隔、执行次数和下次执行时间。无参数。",
)

__all__ = [
    "add_job_tool",
    "get_scheduler",
    "list_jobs_tool",
    "remove_job_tool",
    "set_scheduler",
]
