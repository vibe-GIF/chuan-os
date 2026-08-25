"""Sub-agent 注册表 —— 角色可调用的子 agent 管理。

Sub-agent 是「角色调用外部 agent 干活」的抽象层。
一个角色可以挂载多个 sub-agent，在自己的 ReAct 循环中以 tool 方式调用它们。

三种形态:
- command: 通过 stdin/stdout 子进程执行（如 prime_agent）
- prompt: 纯 prompt 模板，由角色自身的 LLM 处理
- mcp: 通过 MCP 协议调用（预留）
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from chuan.guard import Guard


@dataclass(frozen=True)
class SubAgentSpec:
    """一个 sub-agent 的描述与调用方式。"""

    id: str
    name: str
    type: Literal["command", "prompt", "mcp", "langgraph"]
    description: str = ""
    invoke: dict[str, Any] = field(default_factory=dict)
    timeout: int = 60
    package_path: str | None = None


class SubAgentRegistry:
    """Sub-agent 全局注册表。

    用法:
        registry = SubAgentRegistry(guard)
        registry.register(SubAgentSpec(id="prime_agent", ...))
        result = registry.invoke("prime_agent", "重构这段代码")
    """

    def __init__(self, guard: Guard | None = None) -> None:
        self._guard = guard or Guard()
        self._agents: dict[str, SubAgentSpec] = {}

    # ------------------------------------------------------------------ #
    # 注册与查询
    # ------------------------------------------------------------------ #
    def register(self, spec: SubAgentSpec) -> None:
        """注册一个 sub-agent。"""
        self._agents[spec.id] = spec

    def get(self, id: str) -> SubAgentSpec | None:
        return self._agents.get(id)

    def list(self) -> list[SubAgentSpec]:
        return list(self._agents.values())

    def list_ids(self) -> list[str]:
        return list(self._agents.keys())

    def remove(self, id: str) -> bool:
        return self._agents.pop(id, None) is not None

    # ------------------------------------------------------------------ #
    # 调用
    # ------------------------------------------------------------------ #
    def invoke(self, id: str, task: str, context: dict[str, Any] | None = None) -> str:
        """同步调用一个 sub-agent。

        Args:
            id: sub-agent ID
            task: 交给 sub-agent 的任务文本
            context: 可选上下文（如工作目录、额外参数）

        Returns:
            sub-agent 的回复文本
        """
        spec = self._agents.get(id)
        if spec is None:
            return f"[SUB_AGENT ERROR] unknown sub-agent: {id}"

        # 过 guard
        action = {"type": "sub_agent", "tool": id, "input": task}
        verdict = self._guard.review(id, action)
        if not verdict.approved:
            return f"[GUARD BLOCKED] {verdict.reason}"

        if spec.type == "command":
            return self._invoke_command(spec, task, context)
        if spec.type == "prompt":
            return self._invoke_prompt(spec, task)
        return f"[SUB_AGENT ERROR] unsupported type: {spec.type}"

    def invoke_async(
        self, id: str, task: str, context: dict[str, Any] | None = None
    ) -> str:
        """异步调用（当前为同步包装，后续可改为真异步）。"""
        return self.invoke(id, task, context)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _invoke_command(
        self, spec: SubAgentSpec, task: str, context: dict[str, Any] | None = None
    ) -> str:
        command: list[str] = list(spec.invoke.get("command", []))
        if not command:
            return "[SUB_AGENT ERROR] command type requires invoke.command"

        cwd = None
        if spec.package_path:
            cwd = Path(spec.package_path)
            if not cwd.is_absolute():
                cwd = Path(__file__).resolve().parent.parent.parent / spec.package_path
            if not cwd.exists():
                cwd = None

        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=cwd,
                shell=False,
            )
        except OSError as exc:
            return f"[SUB_AGENT ERROR] {exc}"

        # 手动喂 stdin 并关闭管道：避免 Windows 上 subprocess.run(input=, timeout=)
        # 超时后回收管道时，喂数据线程与 kill+drain 相互死锁（本进程内有前置子进程时必现）。
        def _feed_stdin() -> None:
            try:
                if task:
                    proc.stdin.write(task)  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()  # type: ignore[union-attr]
                except OSError:
                    pass

        feeder = threading.Thread(target=_feed_stdin, daemon=True)
        feeder.start()

        try:
            stdout, stderr = proc.communicate(timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            # 杀掉子进程后再次 communicate() 排空管道，防止句柄泄漏导致挂死
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            feeder.join(timeout=2)
            return f"[SUB_AGENT TIMEOUT] {spec.id} exceeded {spec.timeout}s"

        feeder.join(timeout=2)
        output = (stdout or "").strip()
        if proc.returncode == 0:
            return output or f"[SUB_AGENT COMPLETED] {spec.id}"
        error = (stderr or "").strip() or output or "no output"
        return f"[SUB_AGENT FAILED exit={proc.returncode}] {error}"

    @staticmethod
    def _invoke_prompt(spec: SubAgentSpec, task: str) -> str:
        prompt_template: str = spec.invoke.get("prompt", "")
        return f"{prompt_template}\n\n---\n{task}"