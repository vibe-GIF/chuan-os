"""Agent 池 —— 管理常驻 agent 实例，支持动态 spawn 临时 agent。

岗位（PersonaRole）从池子里取 agent 调用：
- 常驻池：启动时注册，复用（pi / OpenCode / Claude Code / prime_agent）
- 临时 spawn：岗位按需创建内置 ReAct 实例，配不同 prompt 和工具，用完销毁

阶段1：常驻池管理外来 command agent；内置 agent 通过 get_builtin_agent()
按需创建（由 PersonaRole 懒加载持有），不进常驻池。
"""

from __future__ import annotations

from typing import Any

from langgraph.prebuilt import create_react_agent

from chuan.agents.base import AgentInstance
from chuan.agents.builtin import BuiltinAgent
from chuan.agents.command import CommandAgent


class AgentPool:
    """agent 池：常驻实例 + 动态 spawn。"""

    def __init__(
        self,
        persona_loader: Any,
        brain_registry: Any = None,
        guard: Any = None,
        memory: Any = None,
    ) -> None:
        """初始化 agent 池。

        Args:
            persona_loader: PersonaLoader 实例，用于 birth 内置 agent 和读取外来 agent 配置
            brain_registry: BrainRegistry（阶段1暂不直接使用，预留阶段2 spawn）
            guard: Guard（阶段1暂不直接使用，预留阶段2）
            memory: Memory（阶段1暂不直接使用，预留阶段2）
        """
        self._persona_loader = persona_loader
        self._brain_registry = brain_registry
        self._guard = guard
        self._memory = memory
        self._resident: dict[str, AgentInstance] = {}
        self._temp: list[AgentInstance] = []

    # ------------------------------------------------------------------ #
    # 常驻池管理
    # ------------------------------------------------------------------ #
    def register_resident_agents(self) -> None:
        """从外来 agent 配置加载 command agent，注册到常驻池。

        遍历 persona_loader.external_agents 中已启用的外来 agent，
        对声明了 command 的创建 CommandAgent 并注册。
        单个失败不阻断其他 agent 注册。
        """
        external = getattr(self._persona_loader, "external_agents", None)
        if external is None:
            return

        for name in external.list_enabled():
            try:
                spec = external.get(name)
                if spec is None or not spec.command:
                    continue
                agent = CommandAgent(
                    name=name,
                    command=list(spec.command),
                    display_name=spec.definition.get("display_name", name),
                    description=spec.definition.get("description", ""),
                    timeout=spec.timeout_seconds,
                    cwd=str(spec.package_path),
                )
                self._resident[name] = agent
            except Exception as exc:  # noqa: BLE001 - one agent must not block others
                print(f"[WARNING] 常驻 agent '{name}' 注册失败: {exc}")

    def register(self, agent: AgentInstance) -> None:
        """注册一个常驻 agent。"""
        self._resident[agent.name] = agent

    def get(self, name: str) -> AgentInstance | None:
        """从常驻池取 agent（通常是 command agent）。"""
        return self._resident.get(name)

    def list_resident(self) -> list[str]:
        """列出所有常驻 agent 名。"""
        return list(self._resident.keys())

    def get_model(self, persona_name: str) -> Any | None:
        """取 persona 对应 brain 的聊天模型（岗位规划器用）。

        外部 command agent 没有 brain，返回 None（调用方回退单 agent）。
        """
        try:
            persona = self._persona_loader.get_persona(persona_name)
            if persona is None:
                return None
            brain = self._persona_loader.brains.get(persona.brain)
            return getattr(brain, "model", None)
        except Exception:  # noqa: BLE001 - 规划器可选，取不到不致命
            return None

    # ------------------------------------------------------------------ #
    # 内置 agent 按需创建
    # ------------------------------------------------------------------ #
    def get_builtin_agent(self, persona_name: str, checkpointer: Any = None) -> BuiltinAgent:
        """调用 persona_loader.birth() 创建内置 ReAct agent，包装为 BuiltinAgent。

        不注册到常驻池，由调用方（PersonaRole）持有。
        persona_loader.birth() 内部有缓存，重复调用返回同一图实例。

        Args:
            persona_name: persona 名（对应 personas/<name>.yaml）
            checkpointer: 记忆存档

        Returns:
            包装好的 BuiltinAgent 实例
        """
        graph = self._persona_loader.birth(persona_name, checkpointer=checkpointer)
        return BuiltinAgent(graph=graph, name=persona_name)

    # ------------------------------------------------------------------ #
    # N38/N39 岗位化 1:N —— 同 persona 的独立图实例（并行 worker / 按实例配置）
    # ------------------------------------------------------------------ #
    def spawn_builtin_instance(
        self,
        persona_name: str,
        checkpointer: Any = None,
        *,
        model: Any = None,
        tools: list | None = None,
        system_prompt: str | None = None,
    ) -> BuiltinAgent:
        """创建同 persona 的**独立** ReAct agent 实例（1:N worker）。

        与 ``get_builtin_agent`` 的区别：用 ``force_rebirth=True`` 绕过 birth
        缓存，每次返回全新图实例——同人设同工具，但图互不共享，供岗位把
        并行子任务分配到各自独立的 worker 上（避免挤在同一个默认实例）。

        N39 按实例配置：``model``/``tools``/``system_prompt``/``checkpointer``
        可覆盖 persona 默认（None 表示沿用 persona 配置）——每个 worker 可配
        不同的模型、工具子集、系统提示词与实例级会话存档（记忆）。
        """
        graph = self._persona_loader.birth(
            persona_name, checkpointer=checkpointer, force_rebirth=True,
            model=model, tools=tools, system_prompt=system_prompt,
        )
        return BuiltinAgent(graph=graph, name=persona_name)

    # ------------------------------------------------------------------ #
    # 动态 spawn（阶段3实现）
    # ------------------------------------------------------------------ #
    def spawn_builtin(
        self,
        model: Any,
        tools: list | None = None,
        system_prompt: str = "",
        name: str = "temp_builtin",
        checkpointer: Any = None,
    ) -> BuiltinAgent:
        """动态创建一个临时内置 ReAct agent。

        岗位拆分子任务后，需要特定专家视角的子任务 spawn 一个临时实例，
        配不同的 system prompt（"你是测试工程师"）和工具子集。
        工具缺省取 ToolRegistry 全集（ADR-009 全局挂载 + deny 减法）。

        Args:
            model: 聊天模型（通常取 persona 对应 brain 的 model）
            tools: 工具列表；None 则用 ToolRegistry 全集，[] 表示无工具
            system_prompt: 临时 agent 的系统提示词
            name: agent 名（用于日志和结果归属）
            checkpointer: N39 按实例记忆——实例级会话存档；None 不持久化

        Returns:
            包装好的 BuiltinAgent，同时记入临时池（cleanup_temp 统一销毁）
        """
        if tools is None:
            registry = getattr(self._persona_loader, "tools", None)
            tools = registry.get_tools() if registry is not None else []
        graph = create_react_agent(
            model, tools, prompt=system_prompt, name=name, checkpointer=checkpointer
        )
        agent = BuiltinAgent(graph=graph, name=name)
        self._temp.append(agent)
        return agent

    def cleanup_temp(self) -> None:
        """销毁所有临时 agent。"""
        self._temp.clear()
