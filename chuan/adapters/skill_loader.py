"""Skill 加载器 —— 从 skills/*.yaml 加载 Skill 定义。

解析 yaml 中的 trigger / mcp_server / handler，
注册为可调用的 Skill 对象，最终统一为 LangChain Tool 供 Agent 挂载。

ADR-009: 所有 skill/MCP 默认全员挂载；agent 用 deny 做减法。
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import Tool

from chuan.adapters.mcp_adapter import MCPAdapter


class Skill:
    """单个 Skill 的元数据封装。

    Skill 有三种形态:
    - handler: 本地 Python 函数，直接包装为 LangChain Tool
    - mcp:     依赖外部 MCP server，工具从 MCPAdapter 取
    - prompt:  纯提示词模板（N30 自动技能创建：触发词命中时注入复用做法）
    """

    def __init__(self, name: str, definition: dict[str, Any]) -> None:
        self.name: str = name
        self.description: str = definition.get("description", "")
        self.trigger: dict[str, Any] = definition.get("trigger", {})
        self.params: dict[str, Any] = definition.get("params", {})
        # N30 自动技能创建：prompt 型技能的可复用做法（纯提示词模板）
        self.prompt: str = definition.get("prompt", "")

        if "mcp_server" in definition:
            self.kind = "mcp"
            self.target: str | dict[str, Any] = definition["mcp_server"]
        elif "handler" in definition:
            self.kind = "handler"
            self.target = definition["handler"]
        else:
            self.kind = "prompt"
            self.target = None

    # ------------------------------------------------------------------ #
    # prompt 型技能：触发匹配 + 渲染可复用做法（N30）
    # ------------------------------------------------------------------ #
    def matches(self, text: str) -> bool:
        """任务文本命中任一触发关键词即视为匹配（大小写不敏感、子串匹配）。"""
        low = str(text or "").lower()
        for kw in self.trigger.get("keywords") or []:
            if str(kw).lower() in low:
                return True
        return False

    def render_prompt(self) -> str:
        """渲染可注入的可复用做法；无 prompt 返回空串。"""
        return str(self.prompt or "")

    # ------------------------------------------------------------------ #
    # 转为 LangChain Tool（仅 handler 类型）
    # ------------------------------------------------------------------ #
    def to_tool(self) -> Tool | None:
        """将 handler 类型 skill 转换为 LangChain Tool。

        mcp / prompt 类型返回 None（前者走 MCPAdapter，后者非可调用）。
        """
        if self.kind != "handler":
            return None

        handler_cfg: dict[str, str] = self.target  # type: ignore[assignment]
        module_path = handler_cfg.get("module", "")
        func_name = handler_cfg.get("function", "")

        try:
            module = importlib.import_module(module_path)
            func: Callable[..., Any] = getattr(module, func_name)
        except Exception:  # noqa: BLE001 - optional third-party handler failed to import
            # 静默失败：不阻断其他 skill 加载
            return None

        return Tool(
            name=self.name,
            description=self.description,
            func=func,
        )


class SkillRegistry:
    """Skill 全局注册表 —— 加载 skills/ 目录下所有 YAML。

    对外暴露:
        .get(name)          -> Skill | None
        .list_all()         -> [name, ...]
        .get_tools(deny=[]) -> [Tool, ...]   # handler 类型的工具
    """

    def __init__(self, skills_dir: str | Path = "skills") -> None:
        self._skills: dict[str, Skill] = {}
        dir_path = Path(skills_dir)
        if not dir_path.is_absolute():
            dir_path = Path(__file__).resolve().parent.parent.parent / dir_path
        # 把 skills/ 目录加入 sys.path，让 handlers.xxx 可以被 import
        if str(dir_path) not in sys.path:
            sys.path.insert(0, str(dir_path))
        self._load_all(dir_path)

    def _load_all(self, dir_path: Path) -> None:
        if not dir_path.exists():
            return
        for yaml_path in sorted(dir_path.glob("*.yaml")):
            try:
                with yaml_path.open("r", encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f) or {}
                name = data.get("name", yaml_path.stem)
                self._skills[name] = Skill(name, data)
            except Exception:  # noqa: BLE001, S112 - malformed optional skill is skipped
                # YAML 损坏等异常，跳过不阻断
                continue

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #
    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[str]:
        return list(self._skills.keys())

    def add(self, name: str, definition: dict[str, Any]) -> Skill:
        """运行时注册一个新技能（N30 自动技能创建，无需重启即生效）。"""
        skill = Skill(name, definition)
        self._skills[skill.name] = skill
        return skill

    def find_prompt_skill(self, text: str) -> Skill | None:
        """按触发关键词匹配首个 prompt 型技能（N30 复用注入）。

        按注册顺序返回第一个命中；无命中返回 None。
        """
        low = str(text or "").lower()
        for name, skill in self._skills.items():
            if skill.kind != "prompt":
                continue
            for kw in skill.trigger.get("keywords") or []:
                if str(kw).lower() in low:
                    return skill
        return None

    def get_tools(self, deny: list[str] | None = None) -> list[Tool]:
        """返回所有 handler 类型 skill 对应的 LangChain Tool，排除 deny 列表。

        Args:
            deny: 禁用的 skill 名称列表（ADR-009 减法模型）。
        """
        deny_set = set(deny or [])
        tools: list[Tool] = []
        for name, skill in self._skills.items():
            if name in deny_set:
                continue
            if skill.kind == "handler":
                tool = skill.to_tool()
                if tool is not None:
                    tools.append(tool)
        return tools

    def list_mcp_dependencies(self, deny: list[str] | None = None) -> list[str]:
        """返回所有 mcp 类型 skill 依赖的 server 名称列表（去重）。

        用于启动 MCPAdapter 时知道需要连接哪些 server。
        """
        deny_set = set(deny or [])
        deps: set[str] = set()
        for name, skill in self._skills.items():
            if name in deny_set:
                continue
            if skill.kind == "mcp" and isinstance(skill.target, str):
                deps.add(skill.target)
        return sorted(deps)


class ToolRegistry:
    """统一工具注册表 —— 组装 Skill(handler) + MCP 工具，支持 deny 过滤。

    ADR-009 核心实现:
    - 所有 skill/MCP 默认挂载
    - agent 的 persona YAML 中用 deny: [...] 做减法
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        mcp_adapter: MCPAdapter | None = None,
        extra_tools: list | None = None,
    ) -> None:
        self.skills = skill_registry
        self.mcp = mcp_adapter
        self._extra_tools = list(extra_tools or [])

    def register_tools(self, tools: list) -> None:
        """注册额外的普通工具（如内置 @tool 函数）。"""
        self._extra_tools.extend(tools)

    def get_tools(self, deny: list[str] | None = None) -> list[Tool]:
        """获取该 agent 可用的全部工具（skill handler + MCP tools + extra tools）。

        Args:
            deny: persona YAML 中的 deny 列表，skill 名和 MCP server 名都可以写。
        """
        deny_set = set(deny or [])
        tools: list[Tool] = []

        # 1) handler skills
        tools.extend(self.skills.get_tools(deny=list(deny_set)))

        # 2) MCP tools（按 server 名过滤）
        if self.mcp is not None:
            for server_name in self.mcp.connected_servers():
                if server_name not in deny_set:
                    tools.extend(self.mcp.get_tools(server_name))

        # 3) 额外注册的普通工具（按工具名过滤）
        for t in self._extra_tools:
            if t.name not in deny_set:
                tools.append(t)

        return tools

    def list_all_sources(self) -> dict[str, list[str]]:
        """返回所有可用工具来源（调试用）。"""
        return {
            "skills": self.skills.list_all(),
            "mcp_servers": self.mcp.list_servers() if self.mcp else [],
            "mcp_connected": self.mcp.connected_servers() if self.mcp else [],
        }
