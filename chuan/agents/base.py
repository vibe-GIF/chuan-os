"""AgentInstance 基类 —— 所有干活 agent 的统一接口。

岗位（PersonaRole）只跟这个接口交互，不关心 agent 是内置 ReAct 还是外部子进程。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """agent 执行结果的统一封装。"""

    content: str
    agent_name: str
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentInstance(ABC):
    """所有 agent 的基类。

    子类必须实现 async run(task, context) -> AgentResult。
    """

    name: str = "base"
    display_name: str = "Base Agent"
    description: str = ""

    @abstractmethod
    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """执行任务，返回结果。

        Args:
            task: 任务描述
            context: 上下文（工作目录、技术栈、已有文件等）

        Returns:
            AgentResult 封装的执行结果
        """
        ...

    def sync_run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """同步包装，方便非 async 环境调用。"""
        import asyncio

        return asyncio.run(self.run(task, context))
