"""② Session Manager —— 会话生命周期与多会话隔离。

职责：初始化会话持久化（AsyncSqliteSaver），失败时降级为不持久化。
从 RuntimeSupervisor 迁移而来（ADR-012 Gateway 拆分）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor


class SessionManager:
    """会话存档管理：在幕僚长常驻事件循环里初始化 AsyncSqliteSaver。"""

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    def setup_checkpointer(self) -> None:
        """初始化异步会话持久化；失败降级为不持久化。"""
        from concurrent.futures import Future

        memory = self._sup.memory
        if not hasattr(memory, "setup_async"):
            return  # 自定义 Memory 不支持异步初始化
        future: Future = asyncio.run_coroutine_threadsafe(
            memory.setup_async(), self._sup._loop
        )
        try:
            future.result(timeout=10)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING] 会话持久化初始化失败（将不持久化）: {exc}")
            return
        if memory.checkpointer is None:
            print("[WARNING] aiosqlite 未安装，会话将不持久化。pip install aiosqlite")