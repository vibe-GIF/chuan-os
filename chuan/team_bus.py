"""角色总线 —— 成员消息直通（借鉴 dsh-agent-teams 的 mailbox 直达设计）。

dsh 的形态：任意成员 → 任意成员/队长，消息直达对方邮箱并唤醒对方，
无需队长中转。

chuan 的适配（单进程 asyncio，无邮箱概念）：
- ask_role(role, question) 工具：子任务执行中直接向其他岗位提问，
  同步等待该岗位 dispatch 的结果（阻塞在工具调用里，对 agent 透明）
- 深度限制：只允许一层协作（顶层子任务可以问别的岗位，
  被问岗位的子任务不能再往外问）——防 A→B→A 无限递归，
  对应 dsh 的 memberMaxDepth=1
- 角色注册：supervisor wake_up 时把 workers 注册进总线，
  中文名（研究/管家）和英文名（researcher）都能索引
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Callable

from langchain_core.tools import Tool

# 协作深度：0 = 顶层用户请求，1 = 被 ask_role 的岗位内
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("chuan_ask_depth", default=0)

# 角色注册表：name/display_name → dispatch 回调
# dispatch 回调签名: async (task: str, session_id: str) -> str
_roles: dict[str, Callable[..., Any]] = {}


def register_roles(
    workers: dict[str, Any], chief: Any = None
) -> None:
    """注册全部岗位（supervisor wake_up 时调用）。

    每个岗位注册两个 key：英文 name 和中文 display_name。
    """
    _roles.clear()

    def _register(role: Any) -> None:
        name = getattr(role, "name", None)
        display = getattr(role, "display_name", None)
        if name is None:
            return
        _roles[name] = role
        if display and display != name:
            _roles[display] = role

    for role in workers.values():
        _register(role)
    if chief is not None:
        _register(chief)


def clear() -> None:
    """清空注册表（supervisor shutdown 时调用）。"""
    _roles.clear()


def available_roles() -> list[str]:
    """可提问的岗位名列表（去重，展示给模型）。"""
    return sorted({getattr(r, "name", k) for k, r in _roles.items()})


async def _ask_role_async(role: str, question: str) -> str:
    """异步实现：深度校验 → 找岗位 → dispatch → 返回结果。"""
    if _depth.get() >= 1:
        return (
            "[ERROR] 已达最大协作深度（1 层）：被协作岗位不能再向外提问，"
            "请自行基于现有信息完成任务。"
        )
    target = _roles.get(role.strip())
    if target is None:
        names = "、".join(available_roles()) or "（无）"
        return f"[ERROR] 没有岗位叫「{role}」。可用岗位：{names}"
    if not question or not question.strip():
        return "[ERROR] 问题不能为空"

    token = _depth.set(_depth.get() + 1)
    try:
        # 独立协作会话（ask:{role}），不污染用户主会话历史
        reply = await target.dispatch(
            question.strip(), session_id=f"ask:{getattr(target, 'name', role)}"
        )
        # 剥掉「[角色]」前缀，让协作结果干净地回到调用方上下文
        if reply.startswith("[") and "]" in reply:
            reply = reply.split("]", 1)[1].strip()
        return reply
    except Exception as exc:  # noqa: BLE001 - 协作失败返回明确信号
        return f"[ERROR] 岗位「{role}」协作失败：{exc}"
    finally:
        _depth.reset(token)


def _ask_role_sync(role: str, question: str) -> str:
    """同步入口（agent 走同步工具调用时）。

    在事件循环内被直接调用时会失败——LangChain 的 async 路径会
    优先用 coroutine 版本，这里兜底处理无循环的场景。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_ask_role_async(role, question))
    return "[ERROR] 同步上下文无法调用岗位协作，请重试。"


def build_ask_role_tool() -> Tool:
    """构造 ask_role 工具（注册进 ToolRegistry，全员挂载）。"""
    return Tool(
        name="ask_role",
        description=(
            "向班底里的其他岗位提问并等待其答复（成员协作直通）。"
            "例如：调研时让管家岗位查天气、让编程岗位写脚本。"
            "参数 role 是岗位名（如 管家/研究/编程/秘书），question 是具体问题。"
            "注意：只有一层协作深度，被问岗位不能再向外提问。"
        ),
        func=_ask_role_sync,
        coroutine=_ask_role_async,
    )


def current_depth() -> int:
    """当前协作深度（测试用）。"""
    return _depth.get()
