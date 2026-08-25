"""N3 persona 出生 —— 从 YAML 到活着的 agent。

核心定义（本项目的硬约定）:
    agent = 一个能 `.invoke({"messages": [...]})` 的运行中 CompiledStateGraph，
            带 LLM + system_prompt + 工具集 + （可选）记忆。

persona YAML 只是「设备描述符」，不是 agent。
`birth()` 被调用、`create_react_agent(...)` 返回的那一刻，agent 才算活了。

两种 persona 格式并存（向后兼容）:
- 新格式（ADR-009）: `role` + `deny: [...]`  —— 全局工具默认全挂，deny 做减法
- 旧格式:            `tools: [...]` / `skills: [...]` —— 白名单，只挂列出的
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import BaseTool, Tool
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from chuan.adapters.mcp_adapter import MCPAdapter
from chuan.adapters.skill_loader import SkillRegistry, ToolRegistry
from chuan.adapters.sub_agent_registry import SubAgentRegistry
from chuan.brains import BrainRegistry
from chuan.external_agents import ExternalAgentLoader
from chuan.guard import Guard


class Persona:
    """persona 的「设备描述符」—— 读 YAML 得到的静态配置，还不是 agent。"""

    def __init__(self, name: str, definition: dict[str, Any]) -> None:
        self.name: str = name
        self.display_name: str = definition.get("display_name", name)
        self.description: str = definition.get("description", "")
        # None = 未显式指定，birth() 时跟随 routing.default_brain（一处配置全局切脑）
        self.brain: str | None = definition.get("brain")
        self.role: str = definition.get("role", name)

        # ADR-009 新格式：deny 减法
        self.deny: list[str] = definition.get("deny", []) or []

        # 旧格式：tools/skills 白名单（None 表示未使用旧格式）
        self._legacy_tools: list[str] | None = definition.get("tools")
        self._legacy_skills: list[str] | None = definition.get("skills")

        # sub-agent 可调用列表（v2 角色-agent 解耦）
        self.sub_agents: list[str] = definition.get("sub_agents", []) or []

        # 幕僚长专属：路由配置
        self.routing: dict[str, Any] = definition.get("routing", {}) or {}
        self.can_dispatch_to: list[str] = definition.get("can_dispatch_to", []) or []
        self.external: bool = bool(definition.get("external", False))
        self.extra_prompt: str = definition.get("prompt", "")

        # ADR-013 目录格式：personas/<name>/ 下的 SOUL.md / config.yaml
        self.directory: Path | None = None  # 目录格式时指向角色目录
        self.soul: str = ""

        self.raw: dict[str, Any] = definition

    # ------------------------------------------------------------------ #
    @property
    def uses_legacy_allowlist(self) -> bool:
        """是否使用旧的白名单格式（tools/skills 显式列出）。"""
        return self._legacy_tools is not None or self._legacy_skills is not None

    @property
    def legacy_allowlist(self) -> list[str]:
        """旧格式下允许的工具/skill 名称合集。"""
        names: list[str] = []
        if self._legacy_tools:
            names.extend(self._legacy_tools)
        if self._legacy_skills:
            names.extend(self._legacy_skills)
        return names

    def build_system_prompt(self) -> str:
        """生成该 persona 的 system prompt。"""
        lines = [
            f"你是{self.display_name}，chuan-os（川流）班底中的一员。",
            f"职责：{self.description}",
            "你拥有工具调用能力。需要读取文件、查询信息、执行操作时，必须先调用对应工具，绝不能说\"无法读取\"\"无法执行\"或直接编造结果。不确定文件名时先用 list_dir 查看目录。",
        ]
        if self.can_dispatch_to:
            lines.append(
                "你可以把任务派给这些同事：" + "、".join(self.can_dispatch_to) + "。"
            )
        if self.sub_agents:
            names = "、".join(self.sub_agents)
            lines.append(f"你可以调用这些子 agent 来协助工作：{names}。")
        if self.extra_prompt:
            lines.append(self.extra_prompt)
        # ADR-013：SOUL.md 人设（支持 agent 自写补充）
        if self.soul:
            lines.append("\n—— 我的 SOUL ——")
            lines.append(self.soul.strip())
        lines.append("只做自己职责范围内的事；超出范围的，交回幕僚长转派。")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Persona {self.name} brain={self.brain} deny={self.deny}>"


class PersonaLoader:
    """persona 加载器 —— 读 YAML、按需 birth 成活着的 agent。

    用法:
        loader = PersonaLoader(brains, tool_registry)
        agent = loader.birth("lawyer")          # 出生一个
        agent.invoke({"messages": [...]})       # 它活着，能干活
    """

    def __init__(
        self,
        brain_registry: BrainRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        personas_dir: str | Path = "personas",
        external_config_path: str | Path = "config/config.yaml",
        guard: Guard | None = None,
        sub_agent_registry: SubAgentRegistry | None = None,
        mcp_adapter: MCPAdapter | None = None,
        skill_registry: SkillRegistry | None = None,
        memory: Any = None,
    ) -> None:
        self.brains = brain_registry or BrainRegistry()
        self.guard = guard or Guard()

        # 技能注册表与 MCP 适配器
        self.skills = skill_registry or SkillRegistry()
        self.mcp_adapter = mcp_adapter
        # N13：注入长期记忆，让 agent 能调用 remember/recall_memory
        self.memory = memory

        # 工具注册表：优先用外部传入的，否则自行组装（ADR-009 全局挂载 + deny 减法）
        if tool_registry is not None:
            self.tools = tool_registry
        else:
            self.tools = ToolRegistry(self.skills, mcp_adapter=mcp_adapter)

        self._personas: dict[str, Persona] = {}
        self._born: dict[str, CompiledStateGraph] = {}
        self._load_all(personas_dir)
        self.sub_agents = sub_agent_registry or SubAgentRegistry(self.guard)
        self.external_agents = ExternalAgentLoader(
            self.guard, config_path=external_config_path
        )
        self._external_command_agents: set[str] = set()
        for name, definition in self.external_agents.persona_definitions().items():
            if name in self._personas:
                self.external_agents.add_error(name, "name conflicts with an internal persona")
                continue
            self._personas[name] = Persona(name, definition)
            if self.external_agents.get(name) and self.external_agents.get(name).command:
                self._external_command_agents.add(name)
                self._register_external_as_sub_agent(name)

    # ------------------------------------------------------------------ #
    # YAML 加载
    # ------------------------------------------------------------------ #
    def _load_all(self, personas_dir: str | Path) -> None:
        dir_path = Path(personas_dir)
        if not dir_path.is_absolute():
            dir_path = Path(__file__).resolve().parent.parent / dir_path
        if not dir_path.exists():
            return
        # 旧/单文件格式：personas/<name>.yaml
        for yaml_path in sorted(dir_path.glob("*.yaml")):
            try:
                with yaml_path.open("r", encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f) or {}
                name = data.get("name", yaml_path.stem)
                self._personas[name] = Persona(name, data)
            except Exception:  # noqa: BLE001, S112 - malformed persona is skipped
                continue
        # ADR-013 目录格式：personas/<name>/config.yaml + SOUL.md
        # 与 .yaml 同名时以 .yaml 为准（迁移策略：不覆盖旧角色、渐进迁移）
        for config_path in sorted(dir_path.glob("*/config.yaml")):
            role_dir = config_path.parent
            name = role_dir.name
            if name in self._personas:
                continue
            try:
                with config_path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                persona = Persona(data.get("name", name), data)
                persona.directory = role_dir
                soul_file = role_dir / "SOUL.md"
                if soul_file.exists():
                    persona.soul = soul_file.read_text(encoding="utf-8")
                self._personas[persona.name] = persona
            except Exception:  # noqa: BLE001, S112 - malformed persona is skipped
                continue

    # ------------------------------------------------------------------ #
    # Sub-agent 注册
    # ------------------------------------------------------------------ #
    def _register_external_as_sub_agent(self, name: str) -> None:
        """将外部 command agent 注册为 sub-agent。"""
        spec = self.external_agents.get(name)
        if spec is None or spec.command is None:
            return
        from chuan.adapters.sub_agent_registry import SubAgentSpec
        self.sub_agents.register(SubAgentSpec(
            id=name,
            name=spec.definition.get("display_name", name),
            type="command",
            description=spec.definition.get("description", ""),
            invoke={"command": list(spec.command)},
            timeout=spec.timeout_seconds,
            package_path=str(spec.package_path),
        ))

    def _resolve_sub_agent_tools(self, persona: Persona) -> list[BaseTool]:
        """将 persona 的 sub_agents 列表转为 LangChain Tool 列表。"""
        tools: list[BaseTool] = []
        for agent_id in persona.sub_agents:
            spec = self.sub_agents.get(agent_id)
            if spec is None:
                continue
            _registry = self.sub_agents
            tool = Tool(
                name=f"call_{agent_id}",
                description=f"调用 {spec.name} 协助工作。{spec.description}",
                func=lambda task, _id=agent_id, _reg=_registry: _reg.invoke(_id, task),
            )
            tools.append(tool)
        return tools

    # ------------------------------------------------------------------ #
    # 工具解析（ADR-009 减法 vs 旧白名单）
    # ------------------------------------------------------------------ #
    def _resolve_tools(self, persona: Persona) -> list[BaseTool]:
        """算出该 persona 实际该挂的工具集。

        新格式（ADR-009）: 全局工具全挂，减掉 deny。
        旧格式:            只挂 tools/skills 里列出的（白名单）。
        长期记忆工具对所有岗位统一可用（N13 三层闭环）。
        """
        if self.tools is None:
            tools: list[BaseTool] = []
        elif persona.uses_legacy_allowlist:
            # 旧格式：白名单过滤 —— 拿全部工具，只留名字在白名单里的
            # 白名单支持两种匹配：精确工具名（read_file）或 MCP server 名（filesystem）
            # 写 server 名时自动展开为该 server 的所有工具
            allow = set(persona.legacy_allowlist)
            all_tools = self.tools.get_tools(deny=[])
            expanded_allow = set(allow)
            if self.mcp_adapter is not None:
                for name in allow:
                    server_tools = self.mcp_adapter.get_tools(name)
                    if server_tools:
                        for t in server_tools:
                            expanded_allow.add(t.name)
            tools = [t for t in all_tools if t.name in expanded_allow]
        else:
            # 新格式：全挂 + deny 减法
            tools = self.tools.get_tools(deny=persona.deny)

        if self.memory is not None:
            from chuan.memory_tools import (
                build_howto_tools,
                build_memory_tools,
                build_vault_tools,
                build_wiki_tools,
            )

            tools = [*tools, *build_memory_tools(self.memory)]
            # N24：wiki 知识库维护工具（实体归并 + index 检索），所有角色可用
            tools = [*tools, *build_wiki_tools(self.memory)]
            # N26：L3 从做到造知识原子工具（沉淀/复用「怎么做」），所有角色可用
            tools = [*tools, *build_howto_tools(self.memory)]
            # N36：外接知识库检索工具（临时查 Obsidian 等，与内部记忆隔离）
            tools = [*tools, *build_vault_tools(self.memory)]

        # ADR-013 目录格式角色：注入私有 MEMORY.md 读写工具
        if persona.directory is not None:
            from chuan.memory_tools import build_role_memory_tools

            tools = [*tools, *build_role_memory_tools(persona.directory)]
        return tools

    # ------------------------------------------------------------------ #
    # 出生 —— 核心
    # ------------------------------------------------------------------ #
    def birth(
        self,
        name: str,
        *,
        checkpointer: Any = None,
        force_rebirth: bool = False,
        extra_tools: list[BaseTool] | None = None,
        model: Any = None,
        tools: list[BaseTool] | None = None,
        system_prompt: str | None = None,
    ) -> CompiledStateGraph:
        """让一个 persona 出生成为活着的 agent。

        这是「YAML → agent」的唯一入口。返回值是 CompiledStateGraph，
        可以直接 `.invoke({"messages": [...]})`。

        Args:
            name: persona 名（对应 personas/<name>.yaml 的 name 字段）
            checkpointer: 可选记忆存档（N6 接入 InMemorySaver 等）
            force_rebirth: True 则忽略缓存重新出生
            extra_tools: 额外注入的工具列表（如 call_agent 工具）
            model: N39 按实例覆盖——指定模型；None 用 persona 绑定的 brain
            tools: N39 按实例覆盖——指定工具子集（精确替换 persona 工具集，
                不再自动加 sub_agent 工具）；None 用 persona 工具集
            system_prompt: N39 按实例覆盖——指定系统提示词；None 用人设

        Raises:
            KeyError:   persona 不存在
            ValueError: persona 指定的 brain 在 config 里找不到（且未覆盖 model）
        """
        if not force_rebirth and name in self._born:
            return self._born[name]

        if name in self._external_command_agents:
            agent = self.external_agents.build_worker(name, checkpointer=checkpointer)
            self._born[name] = agent
            return agent

        persona = self._personas.get(name)
        if persona is None:
            raise KeyError(
                f"persona '{name}' 不存在。可用: {sorted(self._personas.keys())}"
            )

        # 未显式指定 brain → 跟随 routing.default_brain；N39 可按实例覆盖 model
        if model is None:
            brain = (
                self.brains.get(persona.brain)
                if persona.brain
                else self.brains.default()
            )
            if brain is None:
                raise ValueError(
                    f"persona '{name}' 指定的 brain '{persona.brain}' 未在 config 中配置。"
                    f"可用: {self.brains.list()}"
                )
            model = brain.model

        # N39：按实例覆盖工具集（精确替换）；None 用 persona 工具集（含 sub_agent）
        if tools is not None:
            resolved_tools = list(tools)
        else:
            resolved_tools = self._resolve_tools(persona)
            sub_tools = self._resolve_sub_agent_tools(persona)
            if sub_tools:
                resolved_tools = [*resolved_tools, *sub_tools]
        if extra_tools:
            resolved_tools = [*resolved_tools, *extra_tools]

        # ★ 出生的那一刻 —— 从此它是 agent，不再是 YAML
        agent = create_react_agent(
            model,
            resolved_tools,
            prompt=(
                system_prompt
                if system_prompt is not None
                else persona.build_system_prompt()
            ),
            name=persona.name,
            checkpointer=checkpointer,
        )

        self._born[name] = agent
        return agent

    def birth_all(
        self, *, exclude: list[str] | None = None, checkpointer: Any = None,
        extra_tools: list[BaseTool] | None = None, force_rebirth: bool = False,
    ) -> dict[str, CompiledStateGraph]:
        """批量出生（N4 幕僚长要一次拿到所有 worker）。

        Args:
            exclude: 不出生的 persona 名列表（通常排除 chief_of_staff 自己）
            checkpointer: 传给每个 agent 的记忆存档
            extra_tools: 额外注入到每个 worker 的工具列表
            force_rebirth: True 则忽略缓存重新出生所有 worker
        """
        skip = set(exclude or [])
        result: dict[str, CompiledStateGraph] = {}
        for name in self._personas:
            if name in skip:
                continue
            try:
                result[name] = self.birth(
                    name, checkpointer=checkpointer, extra_tools=extra_tools,
                    force_rebirth=force_rebirth,
                )
            except Exception as exc:  # noqa: BLE001, S112 - one worker must not block startup
                print(f"[WARNING] worker '{name}' 出生失败: {exc}")
                continue
        return result

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get_persona(self, name: str) -> Persona | None:
        """取 persona 的静态配置（未出生）。"""
        return self._personas.get(name)

    def list_personas(self) -> list[str]:
        """列出所有已加载的 persona 名。"""
        return list(self._personas.keys())

    def list_born(self) -> list[str]:
        """列出当前已出生（活着）的 agent 名。"""
        return list(self._born.keys())

    def role_map(self) -> dict[str, str]:
        """返回 role → persona name 映射（N4 路由用）。"""
        return {p.role: name for name, p in self._personas.items()}

    def kill(self, name: str) -> bool:
        """让一个 agent 退场（释放缓存，对应 ADR-007 用完即焚）。"""
        return self._born.pop(name, None) is not None
