"""Command 型 agent —— 通过 stdin/stdout 子进程调用外部 agent。

适用于 pi / OpenCode / Claude Code / prime_agent 等独立程序。
由 AgentPool.register_resident_agents() 注册进常驻池，供岗位通过
显式指定（"用 pi 干…"）或子任务 agent 字段调用。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from chuan.agents.base import AgentInstance, AgentResult


class CommandAgent(AgentInstance):
    """通过子进程调用外部 agent。"""

    def __init__(
        self,
        name: str,
        command: list[str],
        display_name: str = "",
        description: str = "",
        timeout: int = 600,
        cwd: str | None = None,
    ) -> None:
        self.name = name
        self.display_name = display_name or name
        self.description = description
        self._command = command
        self._timeout = timeout
        self._cwd = Path(cwd) if cwd else None

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        cwd = self._cwd
        if context and context.get("cwd"):
            cwd = Path(context["cwd"])

        try:
            # 用线程池跑子进程，避免同步 subprocess.run 阻塞 chuan-event-loop
            # （阶段3 的 asyncio.gather 并行依赖事件循环不被外部 agent 卡死）
            result = await asyncio.to_thread(
                subprocess.run,
                self._command,
                input=task,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=str(cwd) if cwd else None,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                content=f"[TIMEOUT] {self.name} exceeded {self._timeout}s",
                agent_name=self.name,
                success=False,
            )
        except OSError as exc:
            return AgentResult(
                content=f"[ERROR] {exc}",
                agent_name=self.name,
                success=False,
            )

        output = (result.stdout or "").strip()
        if result.returncode == 0:
            return AgentResult(
                content=output or f"[COMPLETED] {self.name}",
                agent_name=self.name,
            )
        error = (result.stderr or "").strip() or output or "no output"
        return AgentResult(
            content=f"[FAILED exit={result.returncode}] {error}",
            agent_name=self.name,
            success=False,
        )
