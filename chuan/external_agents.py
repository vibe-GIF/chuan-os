"""N7 外来 agent 融合 —— 显式注册的 prompt / command 接入。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage
from langchain_core.tools import Tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from chuan.guard import Guard


@dataclass(frozen=True)
class ExternalAgentSpec:
    """一个已显式启用的外来 agent 描述。"""

    name: str
    definition: dict[str, Any]
    package_path: Path
    command: tuple[str, ...] | None = None
    timeout_seconds: int = 60


class ExternalAgentLoader:
    """只加载 ``external_agents.enabled`` 中列出的外来 agent。

    ``command`` 采用 stdin/stdout 协议：用户任务写入标准输入，标准输出作为
    工具结果返回。命令绝不通过 shell 执行，且每次调用先经过 Guard 审核。
    """

    def __init__(
        self,
        guard: Guard,
        *,
        config_path: str | Path = "config/config.yaml",
    ) -> None:
        self.guard = guard
        self.config_path = self._resolve_config_path(config_path)
        self._specs: dict[str, ExternalAgentSpec] = {}
        self._errors: dict[str, str] = {}
        self._load_enabled()

    def list_enabled(self) -> list[str]:
        """返回实际成功加载的 agent 名称，不扫描未启用目录。"""
        return list(self._specs)

    def get(self, name: str) -> ExternalAgentSpec | None:
        return self._specs.get(name)

    def errors(self) -> dict[str, str]:
        """返回配置或包加载错误；单项失败不会影响其他 agent。"""
        return dict(self._errors)

    def add_error(self, name: str, reason: str) -> None:
        """供宿主记录与内置 persona 的冲突，且不暴露内部可变状态。"""
        self._errors[name] = reason

    def persona_definitions(self) -> dict[str, dict[str, Any]]:
        """返回可直接交给 PersonaLoader 的角色定义。"""
        definitions: dict[str, dict[str, Any]] = {}
        for name, spec in self._specs.items():
            definition = dict(spec.definition)
            definition["name"] = name
            definition["external"] = True
            # 外来 agent 同样遵从 ADR-009：空旧白名单不应意外关闭全局工具。
            if not definition.get("tools"):
                definition.pop("tools", None)
            if not definition.get("skills"):
                definition.pop("skills", None)
            if spec.command:
                definition["external_command_tool"] = f"external_{name}_command"
            definitions[name] = definition
        return definitions

    def tools_for(self, name: str) -> list[Tool]:
        """返回该外来 agent 专属的 command 工具（prompt 型则为空）。"""
        spec = self._specs.get(name)
        if spec is None or spec.command is None:
            return []

        return [
            Tool(
                name=f"external_{name}_command",
                description=spec.definition.get("description", f"Run external agent {name}"),
                func=lambda task: self.run_command(name, task),
            )
        ]

    def build_worker(self, name: str, *, checkpointer: Any = None) -> CompiledStateGraph:
        """将 command 型外来 agent 直接编译成可由 Supervisor 路由的 worker。"""
        spec = self._specs.get(name)
        if spec is None or spec.command is None:
            raise ValueError(f"external agent {name!r} does not declare a command")

        def run_external(state: MessagesState) -> dict[str, list[AIMessage]]:
            messages = state.get("messages", [])
            task = ""
            for message in reversed(messages):
                content = getattr(message, "content", "")
                if content:
                    task = str(content)
                    break
            return {"messages": [AIMessage(content=self.run_command(name, task), name=name)]}

        workflow = StateGraph(MessagesState)
        workflow.add_node("run_external", run_external)
        workflow.add_edge(START, "run_external")
        workflow.add_edge("run_external", END)
        return workflow.compile(checkpointer=checkpointer, name=name)

    def run_command(self, name: str, task: str) -> str:
        """运行已注册的 command agent 一次，供其 worker 或兼容 Tool 使用。"""
        spec = self._specs.get(name)
        if spec is None or spec.command is None:
            return f"[EXTERNAL AGENT ERROR] {name} has no command"
        action = {
            "type": "external_command",
            "tool": f"external_{name}_command",
            "command": " ".join(spec.command),
            "input": task,
        }
        verdict = self.guard.review(name, action)
        if not verdict.approved:
            return f"[GUARD BLOCKED] {verdict.reason}"
        try:
            result = subprocess.run(
                list(spec.command),
                input=task,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=spec.package_path,
                timeout=spec.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return f"[EXTERNAL AGENT TIMEOUT] {name} exceeded {spec.timeout_seconds}s"
        except OSError as exc:
            return f"[EXTERNAL AGENT ERROR] {exc}"

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = stdout.strip()
        if result.returncode == 0:
            return output or "[EXTERNAL AGENT COMPLETED]"
        error = stderr.strip() or output or "no output"
        return f"[EXTERNAL AGENT FAILED exit={result.returncode}] {error}"

    def _load_enabled(self) -> None:
        config = self._load_yaml(self.config_path)
        settings = config.get("external_agents", {}) if isinstance(config, dict) else {}
        enabled = settings.get("enabled", []) if isinstance(settings, dict) else []
        if not isinstance(enabled, list):
            self._errors["config"] = "external_agents.enabled must be a list"
            return

        packages = (
            Path(settings.get("path", "agents"))
            if isinstance(settings, dict)
            else Path("agents")
        )
        if not packages.is_absolute():
            packages = self.config_path.parent.parent / packages
        for requested_name in enabled:
            if not isinstance(requested_name, str) or not self._safe_name(requested_name):
                self._errors[str(requested_name)] = "invalid external agent name"
                continue
            try:
                package_path = (packages / requested_name).resolve()
                if packages.resolve() not in (package_path, *package_path.parents):
                    raise ValueError("package path escapes configured agents directory")
                definition = self._load_yaml(package_path / "agent.yaml")
                if not definition:
                    raise ValueError("missing or empty agent.yaml")
                name = definition.get("name", requested_name)
                if name != requested_name or not self._safe_name(name):
                    raise ValueError("agent.yaml name must match enabled entry")
                command = self._parse_command(definition.get("command"))
                timeout = int(definition.get("timeout_seconds", 60))
                if not 1 <= timeout <= 600:
                    raise ValueError("timeout_seconds must be between 1 and 600")
                self._specs[name] = ExternalAgentSpec(
                    name=name,
                    definition=definition,
                    package_path=package_path,
                    command=command,
                    timeout_seconds=timeout,
                )
            except (OSError, TypeError, ValueError) as exc:
                self._errors[requested_name] = str(exc)

    @staticmethod
    def _parse_command(value: Any) -> tuple[str, ...] | None:
        if value is None:
            return None
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(part, str) and part for part in value)
        ):
            raise ValueError("command must be a non-empty list of executable arguments")
        return tuple(value)

    @staticmethod
    def _safe_name(value: str) -> bool:
        return value.replace("_", "").replace("-", "").isalnum()

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise TypeError(f"{path.name} must contain a YAML mapping")
        return data

    @staticmethod
    def _resolve_config_path(config_path: str | Path) -> Path:
        path = Path(config_path)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parent.parent / path
