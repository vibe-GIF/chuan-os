"""N4 幕僚长核心 —— 岗位化调度（阶段1）。

用户唯一入口，负责路由到各类 persona 岗位（PersonaRole）。
阶段1架构：
- _workers: dict[str, PersonaRole] —— 每个 persona 对应一个岗位
- PersonaRole.dispatch() 从 AgentPool 取 agent 执行（单选，不拆分不并行）
- 路由：Orchestrator 关键词匹配 → 幕僚长 LLM 兜底选岗 → 对应岗位 dispatch
- 不再使用 langgraph-supervisor 的 create_supervisor（它需要 CompiledStateGraph）

用法:
    supervisor = RuntimeSupervisor()
    supervisor.wake_up()           # 初始化所有岗位
    result = supervisor.dispatch("帮我看份合同")  # 路由执行
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from chuan.adapters.mcp_adapter import MCPAdapter
from chuan.adapters.skill_loader import SkillRegistry, ToolRegistry
from chuan.adapters.sub_agent_registry import SubAgentRegistry
from chuan.aci import AciPrefetcher
from chuan.agent_pool import AgentPool
from chuan.brains import BrainRegistry
from chuan.gateway.agent_harness import AgentHarness
from chuan.gateway.agent_spawner import AgentSpawner
from chuan.gateway.cron import CronManager
from chuan.gateway.heartbeat import Heartbeat
from chuan.gateway.memory_ops import MemoryOperations
from chuan.gateway.message_router import MessageRouter
from chuan.gateway.session_manager import SessionManager
from chuan.gateway.skill_dispatcher import SkillDispatcher
from chuan.gateway.supervisor_monitor import SupervisorMonitor
from chuan.guard import Guard
from chuan.memory import Memory
from chuan.mission import MissionManager
from chuan.orchestrator import Orchestrator
from chuan.persona_loader import PersonaLoader
from chuan.role import PersonaRole
from chuan.routines import RoutineManager
from chuan.scheduler import ProactiveScheduler
from chuan.skill_creator import SkillCreator

# N32：mission 进度摘要截断上限（字符），防单条看板过长
_MAX_MISSION_SUMMARY = 200


class RuntimeSupervisor:
    """幕僚长 —— 岗位化调度核心。

    生命周期:
        supervisor = RuntimeSupervisor()
        supervisor.wake_up()       # 创建所有岗位 + 注册常驻 agent
        result = supervisor.dispatch(user_message)   # 路由到岗位执行
        supervisor.shutdown()      # 清理资源
    """

    def __init__(
        self,
        brain_registry: BrainRegistry | None = None,
        personas_dir: str = "personas",
        guard: Guard | None = None,
        memory: Memory | None = None,
        config_path: str = "config/config.yaml",
        on_proactive_alert: Any = None,
        on_progress: Any = None,
    ) -> None:
        self.brains = brain_registry or BrainRegistry()
        self.guard = guard or Guard()
        self.memory = memory or Memory()
        self.config_path = config_path
        # 岗位进度回调（默认打印到 stdout；TUI 前端传入自定义回调接管渲染）
        self._on_progress_cb = on_progress or self._print_progress

        # MCP 适配器、技能注册表、子 agent 注册表、统一工具注册表
        self.mcp_adapter = MCPAdapter()
        self.skill_registry = SkillRegistry()
        self.sub_agent_registry = SubAgentRegistry(self.guard)
        self.tool_registry = ToolRegistry(self.skill_registry, mcp_adapter=self.mcp_adapter)
        # 成员消息直通（借鉴 dsh mailbox）：全员挂载 ask_role 工具
        from chuan.team_bus import build_ask_role_tool

        self.tool_registry.register_tools([build_ask_role_tool()])

        self._persona_loader = PersonaLoader(
            brain_registry=self.brains,
            tool_registry=self.tool_registry,
            personas_dir=personas_dir,
            guard=self.guard,
            external_config_path=config_path,
            sub_agent_registry=self.sub_agent_registry,
            mcp_adapter=self.mcp_adapter,
            skill_registry=self.skill_registry,
            memory=self.memory,
        )

        # Agent 池：管理常驻 command agent + 按需创建内置 agent
        self._agent_pool = AgentPool(
            persona_loader=self._persona_loader,
            brain_registry=self.brains,
            guard=self.guard,
            memory=self.memory,
        )

        # N51 工具市场：能力目录 + 运行时按信号裁剪（P3 借鉴 BaiLongma）。
        # enabled=false（默认）时零成本旁路，AgentPool 不过滤（ADR-009 全量挂载不变）。
        from chuan.tool_market import ToolMarket, load_tool_market_cfg

        _tm_cfg = load_tool_market_cfg(config_path)
        self.tool_market = ToolMarket(
            self.tool_registry, self.skill_registry,
            enabled=_tm_cfg["enabled"],
            min_tools=_tm_cfg["min_tools"],
            always=_tm_cfg["always"],
        )
        if _tm_cfg["enabled"]:
            # 市场开启：默认 spawn 只注入上架工具（运行时 enable/disable 生效）
            self._agent_pool.tool_filter = lambda tools: self.tool_market.enabled_tools()

        self._orchestrator: Orchestrator | None = None
        self._workers: dict[str, PersonaRole] = {}
        self._chief_role: PersonaRole | None = None
        self._is_awake: bool = False
        # 巩固 worker 状态（供 TUI 状态栏展示）：None=未运行/运行中，否则为结果描述
        self.consolidation_status: str | None = None
        # N24：wiki 知识库维护状态（供 TUI/CLI 展示）
        self.wiki_status: str | None = None
        # N28 例行任务注册表（磁盘真相 data/routines.json），供 /routine 管理与调度
        self.routines = RoutineManager(self.memory)
        # N30 自动技能创建（自动提炼 → /skill 人工确认 → 写 YAML + 运行时注册）
        self.skill_creator = SkillCreator(
            self.memory, registry=self.tool_registry.skills
        )
        self.scheduler = ProactiveScheduler(
            self.dispatch_to,
            on_alert=on_proactive_alert,
            on_routine_done=self._archive_routine_result,
        )

        # 常驻事件循环线程：MCP 连接和异步工具调用必须在同一个事件循环里，
        # 否则 anyio memory stream 会在循环关闭时报 ClosedResourceError。
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            name="chuan-event-loop",
            daemon=True,
        )
        self._loop_thread.start()

        # Gateway 七大组件（ADR-012）：幕僚长只做组装与协调，具体职责拆分到组件。
        # 组件通过 self（幕僚长引用）访问共享运行时状态，构造时已全部就绪。
        self.message_router = MessageRouter(self)
        self.session_manager = SessionManager(self)
        self.agent_spawner = AgentSpawner(self)
        self.skill_dispatcher = SkillDispatcher(self)
        self.memory_ops = MemoryOperations(self)
        self.heartbeat = Heartbeat(self)
        self.cron = CronManager(self)

        # Agent Harness：后台委派外部 agent（fire-and-forget），复用常驻池
        # N45 事件总线：任务生命周期事件旁路发布（未启用/无 Redis 时 no-op）
        from chuan.bus import get_bus
        from chuan.queue import get_queue

        self.bus = get_bus(config_path)
        self.task_queue = get_queue(config_path)
        self.agent_harness = AgentHarness(self._agent_pool, bus=self.bus)
        # N32 Mission 长任务追踪（磁盘真相 data/missions.json，跨会话看板）
        self.missions = MissionManager(self.memory)
        # 后台任务完成 → 自动回写关联 mission 的进度（旁路）
        self.agent_harness.on_done(self._on_harness_done)

        # P1 监督者：全程监控 worker 执行轨迹，死胡同 → redirect（不干活只看轨迹）
        self.supervisor_monitor = SupervisorMonitor()

        # N33 ACI 预判注入：路由前并行预取 memory + wiki 上下文，注入岗位任务
        self.aci = AciPrefetcher(self.memory)

        # N35 断点续跑：子任务结果缓存（打断后 /resume 复用已完成子任务）
        from chuan.gateway.task_resume import RoleTaskResumeStore

        self.resume_store = RoleTaskResumeStore()

        # N42 岗位间协作：多岗位并行编排器（懒加载）
        self._team_orchestrator: Any = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def wake_up(self, *, exclude: list[str] | None = None) -> None:
        """唤醒幕僚长：创建所有岗位并注册常驻 agent。

        Args:
            exclude: 不创建岗位的 persona 名列表（默认排除 chief_of_staff，
                     幕僚长自己单独存为 _chief_role）
        """
        if self._is_awake:
            return

        # 初始化异步会话持久化（AsyncSqliteSaver 必须在常驻事件循环里创建，
        # 与后续 agent 图 ainvoke 同循环，否则 aiosqlite 跨循环调用会炸）
        self.session_manager.setup_checkpointer()

        # 重建长期记忆 FTS 索引（N13），纳入外部手写文档
        self.memory_ops.reindex()

        # 连接 MCP servers（失败不阻断启动）
        self.skill_dispatcher.connect_mcp()

        if exclude is None:
            exclude = ["chief_of_staff"]

        # 初始化轻量路由器
        self._orchestrator = Orchestrator(self._persona_loader)

        # 注册常驻 command agent（pi / prime_agent / claude_code / opencode）
        self._agent_pool.register_resident_agents()

        # 出生内置 persona 岗位（懒加载，不立即创建内置 agent）+ 幕僚长岗位。
        # 外来 agent（pi/claude_code/opencode/prime_agent）不创建岗位，
        # 只注册到 AgentPool 常驻池，供内置岗位通过显式指定或 sub_agent 工具调用。
        self.agent_spawner.spawn(set(exclude))

        self._is_awake = True
        # 成员消息直通：全员注册进总线（ask_role 按名/显示名索引）
        from chuan import team_bus

        team_bus.register_roles(self._workers, chief=self._chief_role)
        self._report_unfinished_teams()
        # 定时主动任务（管家提醒）
        self.cron.load_scheduled_jobs()
        # N28 例行任务（data/routines.json）→ 注册进调度器并启动自转闭环
        self.routines.apply_to(self.scheduler)
        # L4 记忆闭环：后台异步蒸馏旧会话为持久笔记（失败只告警，不阻断启动）
        self.memory_ops.kickoff_consolidation()
        # N24：wiki 知识库初始化 + 每日维护（建 5 类目录 + lint）
        self.memory_ops.kickoff_wiki_maintenance()
        # N45 事件总线：拉起跨进程监听（幂等；未启用/无 Redis 时 no-op）
        self.bus.start_listener()

    @staticmethod
    def _report_unfinished_teams() -> None:
        """冷恢复提示（借鉴 dsh 磁盘真相）：重启后报告上次未完成的团队任务。"""
        from chuan.team_state import load_unfinished

        try:
            unfinished = load_unfinished()
        except Exception:  # noqa: BLE001 - 提示是旁路
            return
        for doc in unfinished[:3]:
            pending = [st for st in doc.get("subtasks", [])
                       if st.get("status") in ("pending", "running")]
            print(
                f"[INFO] 上次 [{doc.get('role')}] 的任务未完成，剩 {len(pending)} 个子任务："
                f"{doc.get('task', '')[:40]}…（详情 data/teams/）"
            )

    @staticmethod
    def _print_progress(event: dict[str, Any]) -> None:
        """岗位进度打印（阶段3）：多子任务执行时让用户看到中间进展。"""
        kind = event.get("event")
        if kind == "plan":
            print(
                f"  [{event.get('role')}] 拆分为 {event.get('count')} 个子任务"
                f"（{event.get('waves')} 波并行）"
            )
        elif kind == "subtask_start":
            print(f"  ├─ 子任务 {event.get('subtask')}：{event.get('description')}")
        elif kind == "subtask_retry":
            print(f"  ├─ 子任务 {event.get('subtask')} 失败，第 {event.get('attempt')} 次尝试…")
        elif kind == "subtask_done":
            mark = "完成" if event.get("success") else "失败"
            print(f"  ├─ 子任务 {event.get('subtask')} {mark}")

    def shutdown(self) -> None:
        """关闭幕僚长，清理资源。"""
        self.scheduler.stop()
        # 在常驻事件循环里断开 MCP
        future = asyncio.run_coroutine_threadsafe(
            self.mcp_adapter.disconnect_all(), self._loop
        )
        try:
            future.result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        # 关闭异步 checkpointer 的 aiosqlite 连接（同一循环）
        future = asyncio.run_coroutine_threadsafe(
            self.memory.close_async(), self._loop
        )
        try:
            future.result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        # 停止事件循环线程
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        # 清理 agent 池（常驻 command agent + 临时 agent）
        try:
            self._agent_pool.cleanup_temp()
        except Exception:  # noqa: BLE001
            pass
        # 释放 persona_loader 中已 birth 的内置 agent 缓存
        for name in list(self._persona_loader.list_born()):
            self._persona_loader.kill(name)
        self._workers.clear()
        # 角色总线清空（ask_role 不再可用）
        from chuan import team_bus

        team_bus.clear()
        self._chief_role = None
        self._is_awake = False

    # ------------------------------------------------------------------ #
    # 调度接口
    # ------------------------------------------------------------------ #
    def dispatch(
        self,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        session_id: str = "default",
    ) -> dict[str, Any]:
        """分发用户消息到合适的岗位。

        路由优先级:
        1. Orchestrator 关键词匹配（快速，无需 LLM）
        2. 幕僚长 LLM 兜底（语义匹配选岗）
        3. 幕僚长自己处理（LLM 不可用或选岗失败）

        N27 主流程集成：先把「确认/丢弃」消息路由到待确认知识原子
        （不回 agent），dispatch 结束后若新增候选则往回复追加确认提示。

        Args:
            message: 用户输入文本
            history: 可选的历史消息列表
            session_id: 会话 ID

        Returns:
            包含 "messages" 的状态字典，末条为最终回复
        """
        if not self._is_awake:
            raise RuntimeError("幕僚长尚未唤醒。请先调用 wake_up()。")

        # 0) 繁体→简体归一化（Whisper 语音转写常出繁体），再做天气兜底
        message = self.message_router.simplify(message)
        # N27：待确认知识原子的确认/否决（命中即返回，不再走 agent）
        resolved = self._resolve_pending_howto(message)
        if resolved is not None:
            return resolved
        # 0.1) 确定性天气兜底：命中天气意图时注入实况，勿依赖模型调工具
        message = self.message_router.ground_weather(message)

        # N42 团队协作：多岗位并行编排（显式点名 / /team）→ 不走单岗位路由
        team_reply = self._try_team_orchestrate(message, session_id)
        if team_reply is not None:
            return {
                "messages": [{"role": "assistant", "content": team_reply}],
                "route": "team",
                "route_method": "team",
            }

        # N27：dispatch 前快照待确认集，结束后对比追加确认提示
        before = self._howto_pending_names()

        # N33 ACI 预判注入：路由前并行预取 memory + wiki 上下文，注入岗位任务
        # （确定性旁路：预取失败返回空块，绝不影响路由与执行）
        aci_block = self._aci_prefetch_block(message)

        # 1) 先用 Orchestrator 做关键词路由
        target = self._orchestrator.route(message) if self._orchestrator else None
        if target and target in self._workers:
            result = self.dispatch_to(
                target, message, session_id=session_id, aci_context=aci_block
            )
            result["route"] = target
            result["route_method"] = "keyword"
        else:
            # 2) 关键词路由失败，走幕僚长 LLM 兜底选岗
            target = self.message_router.route_with_llm(message, history)
            if target and target in self._workers:
                result = self.dispatch_to(
                    target, message, session_id=session_id, aci_context=aci_block
                )
                result["route"] = target
                result["route_method"] = "llm"
            else:
                # 3) LLM 选岗失败或选了幕僚长自己 → 幕僚长岗位处理
                result = self._dispatch_chief(
                    message, session_id=session_id, aci_context=aci_block
                )
                result["route"] = "chief_of_staff"
                result["route_method"] = "chief"

        self._append_howto_prompt(result, before)
        return result

    def route_preview(self, message: str) -> str | None:
        """关键词路由预览（纯本地匹配，不调 LLM）。

        供 TUI 等前端在 dispatch 前快速显示路由去向。
        返回岗位名；None 表示将走 LLM 兜底选岗。
        """
        if not self._is_awake or self._orchestrator is None:
            return None
        target = self._orchestrator.route(self.message_router.simplify(message))
        if target and target in self._workers:
            return target
        return None

    # ------------------------------------------------------------------ #
    # N42 团队协作：多岗位并行编排（第五台阶）
    # ------------------------------------------------------------------ #
    @property
    def team_orchestrator(self) -> Any:
        """多岗位协同编排器（懒加载）。"""
        if self._team_orchestrator is None:
            from chuan.team_orchestrator import TeamOrchestrator

            self._team_orchestrator = TeamOrchestrator(self)
        return self._team_orchestrator

    def _try_team_orchestrate(self, message: str, session_id: str) -> str | None:
        """检测多岗位意图并并行编排；非团队任务返回 None（走正常路由）。

        触发路径：
        1. ``/team <任务>``：LLM 从岗位班底选 2-4 岗拆分（失败兜底单岗位）；
        2. 自然语言显式点名：「让<研究>、<文案>一起<任务>」（确定性解析）。
        """
        if not self._is_awake:
            return None
        orch = self.team_orchestrator
        plan = None
        if message.startswith("/team "):
            task = message[len("/team "):].strip()
            if task:
                plan = orch.plan_team_llm(task, self._resolve_team_model())
        if plan is None:
            from chuan.team_orchestrator import detect_team_roles

            plan = detect_team_roles(message, orch.roster())
        if plan is None:
            return None
        return orch.orchestrate(plan, session_id=f"team:{session_id}")

    def _resolve_team_model(self) -> Any:
        """取团队拆分用的规划模型：幕僚长优先，否则任意 worker 的模型。"""
        pool = getattr(self, "_agent_pool", None)
        get_model = getattr(pool, "get_model", None)
        if not callable(get_model):
            return None
        try:
            return get_model("chief_of_staff") or next(
                (get_model(n) for n in self._workers if get_model(n) is not None),
                None,
            )
        except Exception:  # noqa: BLE001 - 模型解析失败走显式点名路径
            return None

    def dispatch_to(
        self,
        worker_name: str,
        message: str,
        session_id: str = "default",
        aci_context: str = "",
    ) -> dict[str, Any]:
        """直接调用指定岗位，供受控主动任务（scheduler）使用。

        N33 ACI：``aci_context`` 为路由前预取的上下文注入块（空串不注入），
        透传给岗位 dispatch。

        Args:
            worker_name: 岗位名（persona 名）
            message: 任务消息
            session_id: 会话 ID
            aci_context: ACI 预判注入块（可选）

        Returns:
            包含 "messages" 的状态字典
        """
        if not self._is_awake:
            raise RuntimeError("幕僚长尚未唤醒。请先调用 wake_up()。")
        role = self._workers.get(worker_name)
        if role is None:
            raise KeyError(f"worker '{worker_name}' 不可用")
        future = asyncio.run_coroutine_threadsafe(
            role.dispatch(message, session_id=session_id, aci_context=aci_context),
            self._loop,
        )
        response = future.result(timeout=600)
        return {"messages": [{"role": "assistant", "content": response}]}

    # ------------------------------------------------------------------ #
    # N27 主流程集成：待确认知识原子（确认/否决 + 沉淀后追加提示）
    # ------------------------------------------------------------------ #
    _HOWTO_CONFIRM_WORDS = frozenset(
        {"确认", "批准", "入库", "同意", "好的", "行", "要", "ok", "yes", "y"}
    )
    _HOWTO_DENY_WORDS = frozenset(
        {"丢弃", "拒绝", "不要", "不用", "算了", "删掉", "否", "no", "n"}
    )

    def _resolve_pending_howto(self, message: str) -> dict[str, Any] | None:
        """把用户的确认/否决消息映射到待确认知识原子的 approve/discard。

        仅当存在待确认候选且消息是确认/否决意图时生效；其余返回 None 走
        正常分发。消息形式：裸意图词（多条候选时列清单请指定），或
        「意图 + 名字」（如「确认 部署周报」「丢弃 Obsidian 笔记归档」）。
        全程确定性、旁路 try/except，绝不吞掉正常对话。
        """
        try:
            from chuan.howto import HowToStore

            store = HowToStore(self.memory)
            pending = store.staging_list()
            if not pending:
                return None
        except Exception:  # noqa: BLE001 - 队列不可用不拦截正常分发
            return None

        text = message.strip()
        low = text.lower()
        action: str | None = None
        name: str | None = None
        for w in ("确认", "批准", "入库", "同意"):
            if low.startswith(w):
                action = "approve"
                # 从原文提取名字（保留大小写，仅匹配时忽略大小写）
                name = text[len(w):].lstrip("：: 　").strip() or None
                break
        if action is None:
            for w in ("丢弃", "拒绝", "不要", "不用", "算了", "删掉"):
                if low.startswith(w):
                    action = "discard"
                    name = text[len(w):].lstrip("：: 　").strip() or None
                    break
        if action is None:
            if low in self._HOWTO_CONFIRM_WORDS:
                action = "approve"
            elif low in self._HOWTO_DENY_WORDS:
                action = "discard"
        if action is None:
            return None

        if name:
            cand = next(
                (c for c in pending if c["name"].lower() == name.lower()), None
            )
            if cand is None:
                return self._howto_system_reply(f"（没有名为「{name}」的待确认候选）")
        elif len(pending) == 1:
            cand = pending[0]
        else:
            return self._howto_system_reply(
                f"有 {len(pending)} 条待确认知识原子，请指定一条：\n"
                + self._howto_pending_list_text(pending)
                + "\n回复「确认 <名字>」或「丢弃 <名字>」。"
            )

        try:
            if action == "approve":
                path = store.approve(cand["name"])
                msg = f"已确认沉淀知识原子「{cand['name']}」→ {path}"
            else:
                store.discard(cand["name"])
                msg = f"已丢弃知识原子候选「{cand['name']}」"
        except Exception as exc:  # noqa: BLE001 - 落盘失败也要给用户反馈
            msg = f"（处理失败：{exc}）"
        return self._howto_system_reply(msg)

    def _howto_pending_names(self) -> set[str]:
        """当前待确认候选的名字集合（供 dispatch 前后对比新增）。"""
        try:
            from chuan.howto import HowToStore

            return {c["name"] for c in HowToStore(self.memory).staging_list()}
        except Exception:  # noqa: BLE001
            return set()

    def _append_howto_prompt(self, result: dict[str, Any], before: set[str]) -> None:
        """若本次 dispatch 新增待确认候选，往末条回复追加确认提示。"""
        try:
            newly = self._howto_pending_names() - before
            if not newly:
                return
            messages = result.get("messages") or []
            if not messages:
                return
            last = messages[-1]
            hint = "｜".join(sorted(newly))
            content = str(last.get("content", ""))
            last["content"] = (
                f"{content}\n\n[待确认] 沉淀了可复用知识原子「{hint}」——"
                f"回复「确认」入库或「丢弃」忽略；多条时回复「确认 <名字>」指定。"
            )
        except Exception:  # noqa: BLE001 - 提示失败不影响主流程
            pass

    @staticmethod
    def _howto_system_reply(msg: str) -> dict[str, Any]:
        return {"messages": [{"role": "assistant", "content": msg}],
                "route": "howto_confirm", "route_method": "howto_confirm"}

    @staticmethod
    def _howto_pending_list_text(pending: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"- {c['name']}（{str(c.get('trigger', ''))[:30]}）" for c in pending)

    # ------------------------------------------------------------------ #
    # N28 例行自动化闭环：/routine 运行时管理 + 归档 wiki
    # ------------------------------------------------------------------ #
    def routine_list(self) -> list[dict[str, Any]]:
        """例行任务清单（含下次触发时间与重试状态，供 /routine 面板）。"""
        out: list[dict[str, Any]] = []
        for r in self.routines.list():
            st = self.routines.retry_state(self.scheduler, r.name)
            out.append({
                "name": r.name, "message": r.message, "schedule": r.schedule,
                "agent": r.agent, "archive_to_wiki": r.archive_to_wiki,
                "retries": r.retries, "fail_count": st["fail_count"],
                "next_run": self.routines.next_run(self.scheduler, r.name),
            })
        return out

    def routine_add(
        self,
        name: str,
        message: str,
        schedule: str,
        *,
        agent: str = "housekeeper",
        archive_to_wiki: bool = False,
        retries: int = 0,
    ) -> str:
        """注册一项例行任务并应用到调度器（同名覆盖）。"""
        try:
            r = self.routines.add(
                name, message, schedule, agent=agent,
                archive_to_wiki=archive_to_wiki, retries=retries,
            )
        except ValueError as exc:
            return f"（添加失败：{exc}）"
        try:
            self.routines.apply_to(self.scheduler)
        except Exception:  # noqa: BLE001 - 调度失败也要提示注册结果
            pass
        archive = " · 归档 wiki" if r.archive_to_wiki else ""
        retry = f" · 重试 {r.retries} 次" if r.retries else ""
        return (
            f"已添加例行任务「{r.name}」({r.schedule}) → {r.agent}"
            f"{archive}{retry}"
        )

    def routine_remove(self, name: str) -> str:
        """移除例行任务并取消其调度。"""
        try:
            self.scheduler.remove_job(name)
        except Exception:  # noqa: BLE001
            pass
        ok = self.routines.remove(name)
        return f"已移除例行任务：{name}" if ok else f"（未找到例行任务：{name}）"

    def _archive_routine_result(self, alert: Any) -> None:
        """例行任务完成后：开启 archive_to_wiki 的例行，结果归档 wiki sources/ 原料层。

        归档后由每日 ingest 归位成实体页（N24），让例行输出进入知识库而不是只看一眼。
        """
        if getattr(alert, "error", False):
            return
        try:
            routine = self.routines.get(getattr(alert, "job_name", ""))
            if routine is None or not routine.archive_to_wiki:
                return
            from chuan.wiki import Wiki

            Wiki(self.memory).import_source(
                f"routine-{routine.name}",
                getattr(alert, "content", ""),
                source=f"routine:{routine.name}",
            )
        except Exception:  # noqa: BLE001 - 归档失败不影响主流程
            pass

    def delegate(
        self,
        agent_name: str,
        task: str,
        *,
        session_id: str = "default",
        on_done: Any = None,
        depends_on: list[str] | None = None,
        mission: str = "",
    ) -> str:
        """后台委派一个外部 agent 干活（fire-and-forget），立即返回 task_id。

        借鉴 deepseek-harness：把编码任务整体交给 claude_code / opencode /
        pi / prime_agent 等原生 CLI 子进程黑盒执行，只回收最终结果。
        派发即返，不阻塞主对话；完成/失败通过 agent_harness 注册的
        on_done 回调异步回推（CLI/HUD 等旁路通道自行注册）。
        支持依赖：depends_on 列出的前置任务全部结束后本任务才运行。
        ``mission``（N32）：关联的 Mission 名，任务完成时自动回写其进度。

        Args:
            agent_name: 常驻池里的外部 agent 名
            task: 交给 agent 的任务文本
            session_id: 会话 ID（用于线程隔离）
            on_done: 本次任务独有完成回调
            depends_on: 前置任务 task_id 列表（无则立即运行）
            mission: 关联的 Mission 名（跨对话长任务看板）

        Returns:
            task_id（可用 delegate_snapshot()/agent_harness.get() 查状态）

        Raises:
            RuntimeError: 幕僚长未唤醒
            KeyError: agent_name 不在常驻池
            ValueError: depends_on 非法
        """
        if not self._is_awake:
            raise RuntimeError("幕僚长尚未唤醒。请先调用 wake_up()。")
        if self._agent_pool.get(agent_name) is None:
            raise KeyError(
                f"常驻 agent '{agent_name}' 不可用（可用："
                f"{'、'.join(self._agent_pool.list_resident()) or '无'}）"
            )
        return self.agent_harness.submit(
            agent_name,
            task,
            session_id=session_id,
            loop=self._loop,
            on_done=on_done,
            depends_on=depends_on,
            mission=mission,
        )

    def _on_harness_done(self, info: dict[str, Any]) -> None:
        """后台任务完成 → 自动回写关联 mission 的进度与任务（旁路）。

        只更新进度与 task_ids，不自动终结 mission（finish 由用户显式调用）。
        异常静默吞掉——看板回写是旁路，绝不影响主流程。
        """
        mission = str(info.get("mission") or "")
        if not mission:
            return
        try:
            ok = bool(info.get("success"))
            summary = str(info.get("result") or "").strip()[:_MAX_MISSION_SUMMARY]
            line = f"[{'完成' if ok else '失败'}] {info.get('task_id')}: {summary or '无结果'}"
            self.missions.update(
                mission, progress=line, task_id=str(info.get("task_id") or "")
            )
        except Exception:  # noqa: BLE001 - 看板回写失败不阻断
            pass

    def delegate_snapshot(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """后台委派任务状态快照（TUI 看板数据源）。"""
        return self.agent_harness.snapshot(status=status)

    # ------------------------------------------------------------------ #
    # N33 ACI 预判注入 —— 路由前并行预取上下文，注入岗位任务（/aci 数据源）
    # ------------------------------------------------------------------ #
    def _aci_prefetch_block(self, message: str) -> str:
        """按用户消息预取 memory + wiki 上下文并渲染成注入块（旁路）。

        返回空串表示无命中/不可用；预取本身记入 ``self.aci`` 供面板展示。
        任何异常静默吞掉，绝不阻断路由。
        """
        if self.aci is None:
            return ""
        try:
            self.aci.prefetch(message)
            return self.aci.last_rendered
        except Exception:  # noqa: BLE001
            return ""

    def aci_status(self) -> dict[str, Any]:
        """ACI 预判注入面板数据：最近一次预取各源命中数 + 是否注入。"""
        try:
            return self.aci.stats()
        except Exception:  # noqa: BLE001 - 面板数据失败按空表展示
            return {"memory": 0, "wiki": 0, "total": 0, "injected": False}

    # ------------------------------------------------------------------ #
    # N35 断点续跑 —— 打断不丢工具（/resume 数据源）
    # ------------------------------------------------------------------ #
    def resume_list(self) -> list[dict[str, Any]]:
        """列出所有可恢复的断点档案（供 /resume 面板）。"""
        try:
            return self.resume_store.list_resumable()
        except Exception:  # noqa: BLE001 - 面板数据失败按空表展示
            return []

    def resume_to(self, worker_name: str, session_id: str = "default") -> str:
        """按指定岗位断点续跑：复用上次 plan 与已完成子任务，只跑未完成。

        会话以 resume_store 的档案 session_id 为准；worker 岗位必须存在。
        """
        if not self._is_awake:
            return "（幕僚长尚未唤醒）"
        role = self._workers.get(worker_name)
        if role is None:
            return f"（worker '{worker_name}' 不可用）"
        try:
            cached = self.resume_store.resume_plan(session_id)
        except Exception as exc:  # noqa: BLE001
            return f"（读取断点失败：{exc}）"
        if cached is None:
            return f"（session '{session_id}' 没有可恢复的断点档案）"
        task = str(cached.get("task") or "")
        if not task:
            return f"（session '{session_id}' 断点档案缺少任务文本）"
        try:
            future = asyncio.run_coroutine_threadsafe(
                role.dispatch(task, session_id=session_id, resume=True),
                self._loop,
            )
            response = future.result(timeout=600)
        except Exception as exc:  # noqa: BLE001
            return f"（续跑失败：{exc}）"
        done = sum(1 for r in (cached.get("results") or {}).values()
                   if r.get("success"))
        return f"[续跑完成] {response[:200]}" if done else response

    def resume_clear(self, session_id: str) -> str:
        """清除指定 session 的断点档案。"""
        ok = self.resume_store.clear(session_id)
        return f"已清除断点档案：{session_id}" if ok else (
            f"（未找到断点档案：{session_id}）")

    # ------------------------------------------------------------------ #
    # N32 Mission 长任务追踪 —— 跨对话看板（/mission 数据源）
    # ------------------------------------------------------------------ #
    def mission_start(
        self, name: str, goal: str, *, agent: str = "housekeeper"
    ) -> str:
        """登记一项跨对话长任务目标（同名覆盖）。"""
        try:
            m = self.missions.start(name, goal, agent=agent, source="manual")
        except ValueError as exc:
            return f"（添加失败：{exc}）"
        return f"已登记 Mission「{m.name}」（{m.goal}）"

    def mission_list(self, status: str | None = None) -> list[dict[str, Any]]:
        """Mission 看板清单（含关联后台任务数）。"""
        try:
            return [
                {
                    "name": m.name, "goal": m.goal, "agent": m.agent,
                    "status": m.status, "progress": m.progress,
                    "tasks": len(m.task_ids), "task_ids": m.task_ids,
                    "created": m.created, "updated": m.updated,
                }
                for m in self.missions.list(status=status)
            ]
        except Exception:  # noqa: BLE001 - 面板数据失败按空表展示
            return []

    def mission_finish(self, name: str, summary: str = "", *, success: bool = True) -> str:
        """标记完成/失败并记录结果摘要。"""
        try:
            ok = self.missions.finish(
                name, summary or "", success=success
            )
        except Exception as exc:  # noqa: BLE001
            return f"（更新失败：{exc}）"
        return f"已标记 Mission「{name}」为{'完成' if success else '失败'}" if ok else (
            f"（未找到 Mission：{name}）")

    def mission_pause(self, name: str) -> str:
        try:
            ok = self.missions.pause(name)
        except Exception as exc:  # noqa: BLE001
            return f"（更新失败：{exc}）"
        return f"已暂停 Mission「{name}」" if ok else f"（未找到 Mission：{name}）"

    def mission_resume(self, name: str) -> str:
        try:
            ok = self.missions.resume(name)
        except Exception as exc:  # noqa: BLE001
            return f"（更新失败：{exc}）"
        return f"已恢复 Mission「{name}」" if ok else f"（未找到 Mission：{name}）"

    def mission_remove(self, name: str) -> str:
        try:
            ok = self.missions.remove(name)
        except Exception as exc:  # noqa: BLE001
            return f"（删除失败：{exc}）"
        return f"已删除 Mission「{name}」" if ok else f"（未找到 Mission：{name}）"

    def monitor_status(self) -> dict[str, Any]:
        """P1 监督者快照（TUI /monitor 数据源）：执行轨迹 + 死胡同 + redirect。"""
        return self.supervisor_monitor.snapshot()

    # ------------------------------------------------------------------ #
    # N45 任务队列 + 事件总线 —— 状态（心跳/TUI 数据源）
    # ------------------------------------------------------------------ #
    def bus_status(self) -> dict[str, Any]:
        """N45 事件总线状态（后端/订阅数/发布统计/监听）。"""
        try:
            return self.bus.stats()
        except Exception:  # noqa: BLE001 - 状态失败按空
            return {"enabled": False}

    def queue_status(self) -> dict[str, Any]:
        """N45 任务队列状态（后端/队列数/重试上限）。"""
        try:
            return self.task_queue.stats()
        except Exception:  # noqa: BLE001 - 状态失败按空
            return {"enabled": False}

    # ------------------------------------------------------------------ #
    # N51 工具市场 —— 状态/按信号裁剪（/tools 数据源）
    # ------------------------------------------------------------------ #
    def tool_market_status(self) -> dict[str, Any]:
        """N51 工具市场状态：开关/数量/下架名单 + 全量目录。"""
        try:
            return {**self.tool_market.stats(), "catalog": self.tool_market.catalog()}
        except Exception:  # noqa: BLE001 - 状态失败按空
            return {"enabled": False}

    def tool_market_select(self, task: str, min_tools: int | None = None) -> list[str]:
        """N51 按信号裁剪：给定任务文本，确定性返回相关工具名列表。"""
        try:
            return [t.name for t in self.tool_market.select(task, min_tools=min_tools)]
        except Exception:  # noqa: BLE001 - 失败返回空
            return []

    # ------------------------------------------------------------------ #
    # N27 知识原子自动沉淀 —— 人工确认队列（/howto 数据源）
    # ------------------------------------------------------------------ #
    def howto_staging(self) -> list[dict[str, Any]]:
        """待人工确认的知识原子候选列表（自动沉淀 → staging）。"""
        try:
            from chuan.howto import HowToStore

            return HowToStore(self.memory).staging_list()
        except Exception:  # noqa: BLE001 - 面板数据失败按空表展示
            return []

    def howto_show(self, name: str) -> str:
        """查看单个待确认候选的完整内容。"""
        try:
            from chuan.howto import HowToStore

            cand = HowToStore(self.memory).staging_get(name)
        except Exception:  # noqa: BLE001
            return f"（读取候选失败：{name}）"
        if cand is None:
            return f"（未找到待确认候选：{name}）"
        tools = "、".join(cand.get("tools") or []) or "无"
        return (
            f"候选：{cand['name']}（来源 {cand.get('source', '')}）\n"
            f"触发：{cand.get('trigger', '')}\n"
            f"工具：{tools}\n"
            f"怎么做：\n{cand.get('process', '')}"
        )

    def howto_approve(self, name: str, rename: str | None = None) -> str:
        """人工确认：候选落入 howto 知识库（index/lint/双链全复用）。"""
        try:
            from chuan.howto import HowToStore

            path = HowToStore(self.memory).approve(name, rename)
        except Exception as exc:  # noqa: BLE001
            return f"（确认失败：{exc}）"
        return str(path) if path else f"（未找到待确认候选：{name}）"

    def howto_discard(self, name: str) -> str:
        """人工否决：丢弃候选，不入库。"""
        try:
            from chuan.howto import HowToStore

            ok = HowToStore(self.memory).discard(name)
        except Exception:  # noqa: BLE001
            return f"（丢弃失败：{name}）"
        return f"已丢弃候选：{name}" if ok else f"（未找到待确认候选：{name}）"

    # ------------------------------------------------------------------ #
    # N30 自动技能创建 —— 人工确认队列（/skill 数据源）
    # ------------------------------------------------------------------ #
    def skill_staging(self) -> list[dict[str, Any]]:
        """待人工确认的技能候选列表（自动沉淀 → staging）。"""
        try:
            return self.skill_creator.staging_list()
        except Exception:  # noqa: BLE001 - 面板数据失败按空表展示
            return []

    def skill_show(self, name: str) -> str:
        """查看单个待确认技能候选的完整内容。"""
        try:
            return self.skill_creator.show(name)
        except Exception as exc:  # noqa: BLE001
            return f"（读取候选失败：{name}：{exc}）"

    def skill_approve(self, name: str, rename: str | None = None) -> str:
        """人工确认：候选写成 skills/<name>.yaml 并运行时注册（本会话即生效）。"""
        try:
            path = self.skill_creator.approve(name, rename)
        except Exception as exc:  # noqa: BLE001
            return f"（确认失败：{exc}）"
        return str(path) if path else f"（未找到待确认候选：{name}）"

    def skill_discard(self, name: str) -> str:
        """人工否决：丢弃候选技能，不注册。"""
        try:
            ok = self.skill_creator.discard(name)
        except Exception:  # noqa: BLE001
            return f"（丢弃失败：{name}）"
        return f"已丢弃候选：{name}" if ok else f"（未找到待确认候选：{name}）"

    def skill_status(self) -> dict[str, Any]:
        """技能看板数据：已注册 prompt 技能数 + 待确认候选数。"""
        try:
            registry = self.tool_registry.skills
            registered = [
                n for n in registry.list_all()
                if registry.get(n) is not None and registry.get(n).kind == "prompt"
            ]
        except Exception:  # noqa: BLE001
            registered = []
        try:
            pending = self.skill_creator.staging_list()
        except Exception:  # noqa: BLE001
            pending = []
        return {"registered": len(registered), "pending": len(pending)}

    # ------------------------------------------------------------------ #
    # MCP 管理（TUI 面板数据源 + 运行时启停，不改 yaml）
    # ------------------------------------------------------------------ #
    def mcp_status(self) -> list[dict[str, Any]]:
        """每个已配置 MCP server 的状态快照（连接/工具/错误）。"""
        try:
            return self.mcp_adapter.server_status()
        except Exception:  # noqa: BLE001 - 面板数据失败按空表展示
            return []

    def _mcp_await(self, coro: Any, timeout: float = 60.0) -> Any:
        """在常驻事件循环里执行 MCP 异步操作并同步等待结果。

        必须在唤醒后调用（_loop 已存在）；MCP session 绑定在 _loop 上，
        启停操作必须回到该循环执行。
        """
        if not self._is_awake:
            raise RuntimeError("幕僚长尚未唤醒。请先调用 wake_up()。")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def mcp_connect(self, name: str) -> bool:
        """运行时连接单个 MCP server。返回是否成功。"""
        try:
            return bool(self._mcp_await(self.mcp_adapter.connect_one(name)))
        except Exception as exc:  # noqa: BLE001 - 返回 False 让面板显示错误
            print(f"[WARNING] MCP '{name}' 连接失败: {exc}")
            return False

    def mcp_disconnect(self, name: str) -> bool:
        """运行时断开单个 MCP server。"""
        try:
            self._mcp_await(self.mcp_adapter.disconnect_one(name))
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING] MCP '{name}' 断开失败: {exc}")
            return False

    def mcp_reconnect(self, name: str) -> bool:
        """运行时重连单个 MCP server。"""
        try:
            return bool(self._mcp_await(self.mcp_adapter.reconnect_one(name)))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING] MCP '{name}' 重连失败: {exc}")
            return False

    async def dispatch_async(
        self,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        session_id: str = "default",
    ) -> dict[str, Any]:
        """异步分发（与 dispatch 相同逻辑，支持 await）。"""
        if not self._is_awake:
            raise RuntimeError("幕僚长尚未唤醒。请先调用 wake_up()。")

        # 0) 繁体→简体归一化（Whisper 语音转写常出繁体），再做天气兜底
        message = self.message_router.simplify(message)
        # 0.1) 确定性天气兜底：命中天气意图时注入实况
        message = self.message_router.ground_weather(message)

        # N33 ACI 预判注入：路由前并行预取 memory + wiki 上下文（旁路）
        aci_block = self._aci_prefetch_block(message)

        # 1) Orchestrator 关键词路由
        target = self._orchestrator.route(message) if self._orchestrator else None
        if target and target in self._workers:
            role = self._workers[target]
            response = await role.dispatch(
                message, session_id=session_id, aci_context=aci_block
            )
            return {
                "messages": [{"role": "assistant", "content": response}],
                "route": target,
                "route_method": "keyword",
            }

        # 2) LLM 兜底选岗
        target = self.message_router.route_with_llm(message, history)
        if target and target in self._workers:
            role = self._workers[target]
            response = await role.dispatch(
                message, session_id=session_id, aci_context=aci_block
            )
            return {
                "messages": [{"role": "assistant", "content": response}],
                "route": target,
                "route_method": "llm",
            }

        # 3) 幕僚长自己处理
        return await self._dispatch_chief_async(
            message, session_id=session_id, aci_context=aci_block
        )

    def _dispatch_chief(
        self, message: str, session_id: str = "default", aci_context: str = ""
    ) -> dict[str, Any]:
        """幕僚长自己的岗位处理消息（同步包装）。"""
        if self._chief_role is None:
            return {"messages": [{"role": "assistant", "content": "（幕僚长尚未就绪）"}]}
        future = asyncio.run_coroutine_threadsafe(
            self._chief_role.dispatch(
                message, session_id=session_id, aci_context=aci_context
            ),
            self._loop,
        )
        response = future.result(timeout=600)
        return {"messages": [{"role": "assistant", "content": response}]}

    async def _dispatch_chief_async(
        self, message: str, session_id: str = "default", aci_context: str = ""
    ) -> dict[str, Any]:
        """幕僚长自己的岗位处理消息（异步）。"""
        if self._chief_role is None:
            return {
                "messages": [{"role": "assistant", "content": "（幕僚长尚未就绪）"}],
                "route": "chief_of_staff",
                "route_method": "chief",
            }
        response = await self._chief_role.dispatch(
            message, session_id=session_id, aci_context=aci_context
        )
        return {
            "messages": [{"role": "assistant", "content": response}],
            "route": "chief_of_staff",
            "route_method": "chief",
        }

    # ------------------------------------------------------------------ #
    # 查询接口
    # ------------------------------------------------------------------ #
    @property
    def is_awake(self) -> bool:
        """幕僚长是否已唤醒。"""
        return self._is_awake

    @property
    def workers(self) -> dict[str, PersonaRole]:
        """当前可用的岗位字典。"""
        return dict(self._workers)

    def list_workers(self) -> list[str]:
        """列出当前已加载的岗位名。"""
        return list(self._workers.keys())

    def get_worker(self, name: str) -> PersonaRole | None:
        """按名取岗位。"""
        return self._workers.get(name)

    def get_persona_loader(self) -> PersonaLoader:
        """获取底层的 PersonaLoader（用于高级操作）。"""
        return self._persona_loader

    def get_agent_pool(self) -> AgentPool:
        """获取 AgentPool（用于高级操作）。"""
        return self._agent_pool
