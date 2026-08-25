"""④ Skill Dispatcher —— 工具注册/调度/权限控制。

职责：连接所有已配置的 MCP server；单个失败只告警，不阻断启动。
从 RuntimeSupervisor 迁移而来（ADR-012 Gateway 拆分）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor


class SkillDispatcher:
    """MCP 工具连接与调度：在常驻事件循环里建立 MCP session。"""

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    def connect_mcp(self) -> None:
        """连接所有已配置的 MCP server；单个失败只记录 warning，不阻断启动。"""
        from concurrent.futures import Future

        adapter = self._sup.mcp_adapter
        future: Future = asyncio.run_coroutine_threadsafe(
            adapter.connect_all(), self._sup._loop
        )
        try:
            future.result(timeout=30)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING] MCP 连接过程异常: {exc}")
            return

        for name, err in adapter.connection_errors().items():
            print(f"[WARNING] MCP server '{name}' 连接失败: {err}")

        connected = adapter.connected_servers()
        if connected:
            print(f"[INFO] MCP servers 已连接: {', '.join(connected)}")
        else:
            print("[WARNING] 没有 MCP server 成功连接，agent 将缺少文件/天气等工具")