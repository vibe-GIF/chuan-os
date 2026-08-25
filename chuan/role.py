"""岗位（PersonaRole）—— 角色当岗位用，不直接干活，只做规划/调度/汇总。

核心设计（ADR-014 岗位架构）:
- 岗位 = 项目经理，不写代码，只拆任务、选 agent、协调、汇总
- agent = 外包工程师，从 agent_pool 取，拿到具体子任务干活
- 一个岗位可以调用多个 agent 并行/串行开发

dispatch() 四步:
1. PLAN      → 拆分子任务（含依赖关系）
2. ASSIGN    → 每个子任务选一个 agent
3. EXECUTE   → 按依赖关系执行（无依赖并行，有依赖串行）
4. SUMMARIZE → 汇总结果，人设包装输出

分阶段实现:
- 阶段1（已完成）: 不拆任务，整个任务直接派给一个 agent（单选）
- 阶段2（已完成）: 任务拆分 + 串行执行
- 阶段3（已完成）: 并行调度（拓扑分波 + asyncio.gather）+ 进度跟踪
  （on_progress 回调）+ 临时 spawn（specialist 子任务动态配专家人设）
- 阶段4（N37，已完成）: 岗位化 1:N 过渡（第一台阶）—— 岗位持有 N 个 agent 实例
  （spawn_agent 显式扩容 + agent_count/list_agents）+ 会话级状态隔离
  （progress/团队状态按 session 隔离，同一岗位并行服务多会话互不串扰）
- 阶段5（N38，已完成）: 岗位化 1:N 过渡（第二台阶）—— 1:N 默认启用：
  并行 auto 子任务自动分配独立 worker 实例（同 persona force_rebirth 独立图，
  上限 CHUAN_PARALLEL_WORKERS）+ _resolve_sub_agent 支持岗位实例 id
- 阶段6（N39，已完成）: 岗位化 1:N 过渡（第三台阶）—— 按实例配置工具/模型/记忆：
  RoleAgentConfig（tools/model/system_prompt/checkpointer）贯穿 spawn_agent 与
  worker 路径，birth 支持覆盖参数，实例配置可记录检视
- 阶段7（N40，已完成）: 按任务复杂度选实例 —— config.yaml ``role_instances`` 声明式
  配置（实例声明 + 复杂度档位映射，角色可覆盖），dispatch 单 agent 路径按
  simple/medium/heavy 分级取对应实例（未配置 → 默认实例，向后兼容）
- 阶段8（N41，当前）: 动态实例池与自动扩缩容 —— 岗位实例池配置
  （``role_instances.pool``：min/max 容量 + 空闲 TTL）+ 用量统计（创建/最近使用/次数）
  + 扩容遵守 max 上限 + 开工前自动回收空闲超 TTL 实例（保留 min 下限，按需重建闭环）
  + ``pool_stats`` 供 TUI/心跳观测扩缩容

可靠性设计（免费模型 JSON 不稳的教训）:
- 规划门槛 _should_plan()：短任务/无步骤词不走规划，零额外开销
- 规划输出严格校验：JSON 可解析、id 唯一、依赖存在、无环
- 任何规划失败（模型缺 JSON/解析失败/成环）→ 降级阶段1单 agent，不阻断
- CHUAN_PLAN=0 环境变量可整体关闭规划
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from chuan.agent_pool import AgentPool
from chuan.agents.base import AgentResult
from chuan.agents.builtin import BuiltinAgent
from chuan.team_state import TeamStateWriter


class PlanError(Exception):
    """任务拆分失败（模型输出不合法/依赖成环等），调用方降级单 agent。"""


def _env_int(name: str, default: int) -> int:
    """读环境变量整数，缺省/非法回退默认值。"""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class SubTask:
    """子任务描述。"""

    id: str
    description: str
    agent: str = "auto"  # "auto" / "builtin" / "pi" / "opencode" / ...
    depends_on: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    specialist: str = ""  # 专家人设（如"你是数据分析师"），非空时 spawn 临时 agent


@dataclass
class RoleAgentConfig:
    """岗位 agent 实例的按实例配置（N39：工具 / 模型 / 记忆）。

    用于 ``spawn_agent``/worker 创建实例时覆盖 persona 默认：
    - ``tools``: 工具子集（精确替换 persona 工具集）；None = persona 工具集
    - ``model``: 聊天模型（如 coding 档）；None = persona brain 模型
    - ``system_prompt``: 系统提示词（可注入实例级私有记忆/背景）；空 = persona 人设
    - ``checkpointer``: 实例级会话存档（按实例记忆隔离）；None = 岗位共享
    """

    tools: list | None = None
    model: Any = None
    system_prompt: str = ""
    checkpointer: Any = None


@dataclass
class RolePoolConfig:
    """岗位动态实例池配置（N41：自动扩缩容）。

    - ``min_instances``: 缩容时保留的非默认实例下限（默认实例是岗位身份，永不回收）
    - ``max_instances``: 扩容上限（并行 worker 最多建到 N 个实例）
    - ``idle_ttl``: 空闲超过该秒数 → 自动缩容回收（再次需要时按需重建）
    """

    min_instances: int = 1
    max_instances: int = 3
    idle_ttl: float = 300.0


@dataclass
class _InstanceStat:
    """岗位实例的用量统计（N41 动态池扩缩容依据）。"""

    created_at: float = 0.0
    last_used_at: float = 0.0
    uses: int = 0


# 规划门槛：出现步骤词，或任务足够长，才值得花一次 LLM 调用做拆分
_PLAN_MARKERS = (
    "然后", "接着", "随后", "之后", "第一步", "第二步", "其次", "最后",
    "同时", "以及", "分别", "再帮", "再写", "再查", "再生成", "并生成",
)
_PLAN_MIN_LEN = 24  # 字符数
_MAX_SUBTASKS = 6


class PersonaRole:
    """岗位类 —— 规划/调度/汇总，不直接执行。

    阶段3：dispatch() 对复合任务走 PLAN → EXECUTE（分波并行）→ SUMMARIZE，
    简单任务/规划失败保持阶段1的单 agent 行为。
    - 用户显式指定 "用 pi 干" → 调常驻 command agent（不规划）
    - 子任务带 specialist 人设 → spawn 临时专家 agent（按人设缓存复用）
    - 同波无依赖子任务 asyncio.gather 并行，波间按依赖串行
    - 进度：on_progress 回调 + self.progress 状态表
    """

    # 显式指定 agent 的关键词映射（小写匹配）
    _EXPLICIT_AGENT_MAP: list[tuple[str, str]] = [
        ("用pi", "pi"),
        ("用 pi", "pi"),
        ("用opencode", "opencode"),
        ("用 opencode", "opencode"),
        ("用claude", "claude_code"),
        ("用 claude", "claude_code"),
        ("用prime", "prime_agent"),
        ("用 prime", "prime_agent"),
    ]

    def __init__(
        self,
        persona,
        agent_pool: AgentPool,
        checkpointer=None,
        planner_model=None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        teams_root: str | None = None,
        monitor=None,
        memory=None,
        resume_store=None,
        instance_config: Any = None,
        pool_config: RolePoolConfig | None = None,
    ) -> None:
        """初始化岗位。

        Args:
            persona: Persona 静态配置对象（含 name / display_name / description）
            agent_pool: AgentPool 实例，用于取常驻 command agent 和创建内置 agent
            checkpointer: 记忆存档，传给内置 agent
            planner_model: 规划用聊天模型；None 则首次 dispatch 时从 pool 解析
            on_progress: 进度回调（阶段3）。收到事件 dict：
                {"event": "plan", ...} 拆分完成
                {"event": "subtask_start" | "subtask_done", ...} 子任务级
                回调抛异常不影响执行
            monitor: P1 监督者（SupervisorMonitor）—— 记录执行轨迹、
                重试前检测死胡同并应用 redirect；None 表示关闭监控（旁路）
            memory: N26 L3 从做到造 —— Memory 实例（提供 HowToStore），
                用于任务开工前自动注入「参考做法」；None 表示关闭复用注入
            instance_config: N40 声明式实例配置（RoleInstanceConfig），
                按任务复杂度选实例；None = 关闭（向后兼容 1:1 默认实例）
            pool_config: N41 动态实例池配置（容量 + 空闲回收 TTL）；
                None 则取 instance_config.pool，仍无则关闭自动扩缩容
        """
        self.persona = persona
        self.name: str = getattr(persona, "name", str(persona))
        self.display_name: str = getattr(persona, "display_name", self.name)
        self.description: str = getattr(persona, "description", "")
        self.pool: AgentPool = agent_pool
        # N40 声明式实例配置（config.yaml role_instances 解析结果）
        self._instance_config = instance_config
        # N41 动态实例池配置：显式传入优先，否则取声明式配置的 pool 段
        self._pool_config = pool_config
        if self._pool_config is None and instance_config is not None:
            self._pool_config = getattr(instance_config, "pool", None)
        self._checkpointer = checkpointer
        self._planner_model = planner_model
        self.on_progress = on_progress
        self._memory = memory
        self._howto_store: Any = None
        # N27 自动沉淀提炼器（懒加载）
        self._distiller: Any = None
        # N30 自动技能创建（懒加载）
        self._skill_creator: Any = None
        # P1 监督者（旁路）：记录轨迹 + 死胡同 redirect；None = 关闭
        self._monitor = monitor

        # 懒加载：第一次 dispatch 时才创建，避免启动时一次性建17个 agent
        # （默认实例在 N37 _agents 池里按 "default" 懒加载）
        # specialist 临时 agent 缓存（按人设复用，避免重复 spawn）
        self._specialists: dict[str, Any] = {}
        # 团队状态落盘（dispatch 规划成功时创建，磁盘真相）；按会话隔离，
        # 同一岗位并行服务多会话时各写各的 data/teams/<session_id>.json
        self._state_writers: dict[str, TeamStateWriter] = {}
        # 落盘根目录（测试注入 tmp_path；None = 项目 data/teams/）
        self._teams_root = teams_root
        # N35 断点续跑：子任务结果缓存（打断后 /resume 复用已完成子任务）
        self._resume_store = resume_store

        # N37 岗位化 1:N 过渡：岗位持有 N 个 agent 实例（1:1 → 1:N）
        # _agents: instance_id → agent（"default" 为默认实例，向后兼容）
        self._agents: dict[str, Any] = {}
        # N39 按实例配置：记录各实例创建时的配置（工具/模型/记忆），供检视审计
        self._agent_configs: dict[str, RoleAgentConfig] = {}
        # N39 按实例配置：并行 worker 的默认配置（None = 沿用 persona 全配置）
        self._worker_config: RoleAgentConfig | None = None
        # N41 动态实例池：实例用量统计（created_at/last_used_at/uses），扩缩容依据
        self._instance_stats: dict[str, _InstanceStat] = {}
        # 会话级进度隔离：同一岗位可并行服务多个会话（微信/CLI/语音），
        # 各会话进度互不串扰；self.progress 保留为「最近会话」视图（兼容）
        self._session_progress: dict[str, dict[str, str]] = {}

        # 岗位状态：项目上下文 + 子任务进度（id → pending/running/done/failed）
        self.context: dict[str, Any] = {}
        self.progress: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 核心调度
    # ------------------------------------------------------------------ #
    async def dispatch(
        self,
        task: str,
        session_id: str = "default",
        aci_context: str = "",
        resume: bool = False,
    ) -> str:
        """岗位核心方法：接收任务，返回最终结果。

        阶段2：复合任务走规划→串行执行→汇总；简单任务或规划失败
        保持阶段1的单 agent 行为（可靠兜底）。

        P1 监督者：整条 dispatch 记一条执行轨迹（trace_id=session_id），
        任何路径结束都标记轨迹完成（finally 保证）。

        N33 ACI 预判注入：``aci_context`` 为路由前预取的上下文注入块
        （memory + wiki 命中），非空时前置到任务文本，agent 首轮直接
        带着相关背景开工。

        N35 断点续跑：``resume=True`` 时若该 session 存在上次规划档案，
        复用其 plan 与已完成子任务结果，只重跑未完成部分（打断不丢工具）。

        Args:
            task: 用户任务描述
            session_id: 会话 ID，用于 LangGraph checkpointer 线程隔离
            aci_context: ACI 预判注入块（空串则不注入）
            resume: 是否为断点续跑（复用上次 plan 与已完成子任务）

        Returns:
            人设包装后的回复字符串
        """
        self._begin_trace(session_id)
        try:
            return await self._dispatch_inner(task, session_id, aci_context, resume)
        finally:
            self._finish_trace(session_id)

    async def _dispatch_inner(
        self,
        task: str,
        session_id: str = "default",
        aci_context: str = "",
        resume: bool = False,
    ) -> str:
        """dispatch 的实际执行体（由 dispatch 包裹轨迹生命周期）。

        N33 ACI：把路由前预取的上下文块前置到任务文本（仅本岗位单次生效，
        不污染调用方传入的原始 task）。
        """
        if aci_context:
            task = f"{aci_context}\n\n{task}"
        # N41 自动缩容：每次开工前回收空闲超 TTL 的实例（仅开启动态池时，旁路）
        self._maybe_reclaim_idle()
        # 1. 用户显式指定 agent → 直接派发，不规划（明确指令不需要拆）
        explicit_name = self._detect_explicit_agent(task)
        if explicit_name:
            agent = self.pool.get(explicit_name)
            if agent is not None:
                clean_task = self._strip_explicit_prefix(task, explicit_name)
                # N26/N30：开工前注入「参考做法」（技能触发命中优先，知识原子兜底）
                clean_task = self._inject_reference(clean_task)
                result: AgentResult = await agent.run(
                    clean_task,
                    context={
                        **self.context, "thread_id": session_id,
                        "__on_progress__": self._emit_progress,
                    },
                )
                self._record_step(
                    session_id, f"explicit:{explicit_name}", 0,
                    getattr(agent, "name", explicit_name),
                    result.success, result.content,
                )
                return self._wrap_result(result, task)

        # 2. 规划门槛：明显简单的任务直接单 agent，省一次规划调用
        if self._should_plan(task):
            try:
                plan = await self._plan(task)
            except PlanError:
                plan = []
            if len(plan) > 1:
                # N35 断点续跑：resume 时复用缓存 plan + 已完成子任务结果
                resume_hits: dict[str, Any] = {}
                if resume and self._resume_store is not None:
                    cached = self._resume_store.resume_plan(session_id)
                    if cached and cached.get("task") == task:
                        plan = self._rehydrate_plan(cached["plan"])
                        resume_hits = dict(cached.get("results") or {})
                self._session_progress_view(session_id).update(
                    {st.id: "pending" for st in plan}
                )
                self._emit_progress(
                    {"event": "plan", "role": self.display_name, "task": task,
                     "count": len(plan), "waves": self._count_waves(plan)}
                )
                # 团队状态落盘（磁盘真相）：规划落定即建档案，状态变更实时更新。
                # 按会话隔离（1:N）：各会话各写各的 <session_id>.json
                writer = TeamStateWriter(
                    role=self.display_name, task=task, session_id=session_id,
                    root=self._teams_root,
                )
                self._state_writers[session_id] = writer
                writer.init_subtasks(
                    [{"id": st.id, "description": st.description} for st in plan]
                )
                # 新规划落盘（非 resume 时才覆盖档案；resume 保持原档案继续写）
                if not resume and self._resume_store is not None:
                    self._resume_store.save_plan(
                        session_id, self.display_name, task,
                        [{"id": st.id, "description": st.description,
                          "agent": st.agent, "depends_on": st.depends_on}
                         for st in plan],
                    )
                results = await self._execute(
                    plan, session_id, task=task, resume_hits=resume_hits
                )
                success = all(r.success for r in results.values())
                merged = AgentResult(
                    content=self._summarize(task, plan, results),
                    agent_name=self.name,
                    success=success,
                )
                writer.finish()
                self._emit_progress(
                    {"event": "done", "role": self.display_name, "success": success}
                )
                return self._wrap_result(merged, task)
            # 拆不出多个子任务（模型判定简单）→ 落回单 agent

        # 3. 单 agent（阶段1 行为，也是一切规划失败的兜底）
        # N40：按任务复杂度选实例（heavy → 声明式重型实例；其余默认）
        tier = self._classify_complexity(task)
        agent = self._resolve_tier_instance(tier)
        # N41：实例实际执行一次 → 更新最近使用/次数（扩缩容依据）
        self._touch_agent(agent)
        # N26/N30：开工前注入「参考做法」（技能触发命中优先，知识原子兜底）
        run_task = self._inject_reference(task)
        result = await agent.run(
            run_task,
            context={
                **self.context, "thread_id": session_id,
                "__on_progress__": self._emit_progress,
            },
        )
        self._record_step(
            session_id, "single", 0, getattr(agent, "name", self.name),
            result.success, result.content,
        )
        return self._wrap_result(result, task)

    # ------------------------------------------------------------------ #
    # 阶段2：任务拆分
    # ------------------------------------------------------------------ #
    def _should_plan(self, task: str) -> bool:
        """规划门槛：含步骤词或足够长的任务才值得拆分。"""
        if os.environ.get("CHUAN_PLAN", "1") == "0":
            return False
        text = task.strip()
        return len(text) >= _PLAN_MIN_LEN or any(m in text for m in _PLAN_MARKERS)

    def _planner_prompt(self, task: str) -> str:
        """构造规划提示词（要求严格 JSON 输出）。"""
        resident = ", ".join(self.pool.list_resident()) or "无"
        return (
            "你是任务规划器。判断下面的任务是否需要拆分成多个子任务。\n"
            "\n"
            "规则：\n"
            "- 简单任务（一次问答/一次查询/一步操作）→ 只返回 1 个子任务\n"
            "- 复合任务（有先后步骤、多方面调研、需要多个工具）→ 拆成 2-"
            f"{_MAX_SUBTASKS} 个子任务\n"
            "- 无依赖的子任务会被并行执行，无先后要求就不要加依赖\n"
            f"- agent 字段填 \"auto\"（默认）或常驻 agent 名（{resident}，"
            "仅当子任务明确需要该工具时）\n"
            "- specialist 字段：仅当子任务需要特定专家视角时填一句人设"
            "（如\"你是数据分析师\"），默认空字符串\n"
            "- depends_on 只能引用其他子任务的 id，表示必须先完成\n"
            "\n"
            "只输出 JSON，不要任何其他文字：\n"
            '{"subtasks": [{"id": "s1", "description": "子任务描述", '
            '"agent": "auto", "depends_on": [], "specialist": ""}]}\n'
            "\n"
            f"任务：{task}"
        )

    async def _plan(self, task: str) -> list[SubTask]:
        """LLM 任务拆分 + 严格校验。失败抛 PlanError（调用方降级单 agent）。"""
        model = self._resolve_planner()
        if model is None:
            raise PlanError("规划模型不可用")

        try:
            resp = await model.ainvoke(self._planner_prompt(task))
        except Exception as exc:  # noqa: BLE001 - 模型调用失败降级
            raise PlanError(f"规划调用失败: {exc}") from exc
        raw = getattr(resp, "content", None) or str(resp)

        data = self._parse_plan_json(str(raw))
        return self._validate_plan(data)

    def _resolve_planner(self) -> Any:
        """解析规划模型：显式传入优先，否则从 pool 按 persona 的 brain 取。"""
        if self._planner_model is None:
            get_model = getattr(self.pool, "get_model", None)
            self._planner_model = get_model(self.name) if callable(get_model) else None
        return self._planner_model

    @staticmethod
    def _parse_plan_json(raw: str) -> Any:
        """从模型回复中抽取 JSON（容忍 ```json 围栏和前后废话）。"""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            raise PlanError("规划输出中找不到 JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise PlanError(f"规划 JSON 解析失败: {exc}") from exc

    @staticmethod
    def _validate_plan(data: Any) -> list[SubTask]:
        """校验规划输出：结构、数量、id 唯一、依赖存在、无环。"""
        if not isinstance(data, dict):
            raise PlanError("规划输出不是 JSON 对象")
        items = data.get("subtasks")
        if not isinstance(items, list) or not (1 <= len(items) <= _MAX_SUBTASKS):
            raise PlanError("subtasks 必须是 1-6 个元素的列表")

        subtasks: list[SubTask] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise PlanError(f"子任务 #{i} 不是对象")
            desc = str(item.get("description", "")).strip()
            if not desc:
                raise PlanError(f"子任务 #{i} 缺少 description")
            subtasks.append(
                SubTask(
                    id=str(item.get("id") or f"s{i + 1}"),
                    description=desc,
                    agent=str(item.get("agent") or "auto"),
                    depends_on=[str(d) for d in item.get("depends_on") or []],
                    specialist=str(item.get("specialist") or "").strip(),
                )
            )

        ids = [st.id for st in subtasks]
        if len(ids) != len(set(ids)):
            raise PlanError("子任务 id 重复")
        id_set = set(ids)
        for st in subtasks:
            if st.agent == st.id:  # agent 误填了自身 id
                st.agent = "auto"
            for dep in st.depends_on:
                if dep not in id_set:
                    raise PlanError(f"子任务 '{st.id}' 依赖不存在的 '{dep}'")
                if dep == st.id:
                    raise PlanError(f"子任务 '{st.id}' 依赖自己")
        PersonaRole._topo_order(subtasks)  # 成环在此抛 PlanError
        return subtasks

    @staticmethod
    def _rehydrate_plan(cached_plan: list[dict[str, Any]]) -> list[SubTask]:
        """从缓存档案重建 plan（resume 用）：把 dict 列表转回 SubTask。

        复用上次规划而非重新 LLM 规划——保证子任务 id/依赖与已缓存结果对齐，
        这是「打断不丢工具、只重跑未完成」的前提。
        """
        subtasks: list[SubTask] = []
        for i, item in enumerate(cached_plan):
            subtasks.append(
                SubTask(
                    id=str(item.get("id") or f"s{i + 1}"),
                    description=str(item.get("description", "")),
                    agent=str(item.get("agent") or "auto"),
                    depends_on=[str(d) for d in item.get("depends_on") or []],
                )
            )
        if not subtasks:
            raise PlanError("缓存档案没有可用 plan")
        return subtasks

    @staticmethod
    def _topo_order(plan: list[SubTask]) -> list[SubTask]:
        """Kahn 拓扑排序；成环抛 PlanError。同层保持原顺序（稳定）。"""
        by_id = {st.id: st for st in plan}
        indegree = {st.id: len(set(st.depends_on)) for st in plan}
        dependents: dict[str, list[str]] = {st.id: [] for st in plan}
        for st in plan:
            for dep in set(st.depends_on):
                dependents[dep].append(st.id)

        ready = [st.id for st in plan if indegree[st.id] == 0]
        order: list[SubTask] = []
        while ready:
            sid = ready.pop(0)
            order.append(by_id[sid])
            for nxt in dependents[sid]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if len(order) != len(plan):
            raise PlanError("子任务依赖成环")
        return order

    # ------------------------------------------------------------------ #
    # 阶段3：分波并行执行
    # ------------------------------------------------------------------ #
    @staticmethod
    def _count_waves(plan: list[SubTask]) -> int:
        """按依赖分波：每波内子任务互不依赖，可并行执行。"""
        done: set[str] = set()
        remaining = list(plan)
        waves = 0
        while remaining:
            ready = [st for st in remaining if set(st.depends_on) <= done]
            if not ready:  # 无环保证不会走到；防御死循环
                break
            waves += 1
            done.update(st.id for st in ready)
            remaining = [st for st in remaining if st.id not in done]
        return max(waves, 1)

    async def _execute(
        self,
        plan: list[SubTask],
        session_id: str = "default",
        task: str = "",
        resume_hits: dict[str, Any] | None = None,
    ) -> dict[str, AgentResult]:
        """按拓扑分波执行：同一波内子任务 asyncio.gather 并行，波间串行。

        每个子任务独立 thread（`{session_id}:plan:{id}`），避免对话历史
        互相污染；总任务背景和依赖上下文（前波结果）通过提示词显式传递。
        单个子任务失败不阻断（无依赖的后继仍执行），汇总时统一标注。

        N35 断点续跑：``resume_hits`` 为上次执行已完成的子任务结果映射
        （subtask_id → {success, content}），命中成功结果时直接复用，
        跳过 agent 调用——只重跑未完成部分（打断不丢工具）。

        N38 岗位化 1:N：本波有 ≥2 个并行 auto 子任务时，各分配一个独立
        worker 实例（``_assign_wave_instances``），避免挤在同一默认实例。
        """
        resume_hits = resume_hits or {}
        results: dict[str, AgentResult] = {}
        remaining: dict[str, SubTask] = {st.id: st for st in plan}
        wave = 0
        while remaining:
            wave += 1
            ready = [
                st for st in remaining.values()
                if set(st.depends_on) <= set(results.keys())
            ]
            if not ready:  # _validate_plan 已验无环；防御性中止
                break
            # N38 1:N 默认启用：并行 auto 子任务 → 独立 worker 实例
            assignments = self._assign_wave_instances(ready)
            gathered = await asyncio.gather(
                *(
                    self._run_subtask(
                        st, results, session_id, wave, task, resume_hits,
                        instance=assignments.get(st.id),
                    )
                    for st in ready
                )
            )
            for st, result in zip(ready, gathered):
                results[st.id] = result
                del remaining[st.id]
        return results

    def _subtask_prompt(
        self, st: SubTask, task: str, results: dict[str, AgentResult]
    ) -> str:
        """构造子任务提示：总任务背景 + 子任务 + 依赖结果 + 输出要求。

        子任务跑在独立 thread（无对话历史），裸任务会让免费模型退化成
        直接输出工具调用文本或贴原始 JSON——必须显式给背景和输出约束。
        """
        parts: list[str] = []
        if task:
            parts.append(f"【总任务】用户请求：{task}")
        # N26/N30：子任务开工前注入「参考做法」（技能触发命中优先，知识原子兜底）
        parts.append(f"【你的子任务】{self._inject_reference(st.description)}")
        dep_parts = [
            f"【{dep} 的结果】\n{results[dep].content}"
            for dep in st.depends_on
            if dep in results
        ]
        if dep_parts:
            parts.append("【前置子任务结果】\n" + "\n\n".join(dep_parts))
        parts.append(
            "【输出要求】面向用户输出完整的中文答复：如需用工具就调用，"
            "然后基于工具结果整理出结论和内容；禁止输出工具调用代码"
            "（如 list_dir(...)）、原始 JSON 返回值或空泛的拒答。"
        )
        return "\n\n".join(parts)

    async def _run_subtask(
        self,
        st: SubTask,
        results: dict[str, AgentResult],
        session_id: str,
        wave: int,
        task: str = "",
        resume_hits: dict[str, Any] | None = None,
        instance: Any = None,
    ) -> AgentResult:
        """执行单个子任务：构造增强提示 → 选 agent → run → 退化检测 → 重试 → 进度上报。

        attempt 机制（借鉴 dsh-agent-teams）：失败自动重跑（默认重试 1 次），
        低价/免费模型的工具调用偶发失败重跑即过，不值得整个子任务判死。
        CHUAN_SUBTASK_RETRIES 可调（0 = 关闭）。

        N35 断点续跑：``resume_hits`` 命中且上次结果成功时直接复用缓存结果
        返回（不再调 agent），只重跑未完成/失败子任务（打断不丢工具）。

        N38 岗位化 1:N：``instance`` 非空时直接用该实例（并行 worker，
        由 ``_assign_wave_instances`` 预分配），否则走 ``_resolve_sub_agent``。
        """
        prog = self._session_progress_view(session_id)
        prog[st.id] = "running"
        self._emit_progress(
            {"event": "subtask_start", "role": self.display_name,
             "subtask": st.id, "description": st.description, "wave": wave}
        )

        # N35 断点续跑：复用上次成功结果，跳过 agent 调用
        if resume_hits:
            cached = resume_hits.get(st.id)
            if cached and cached.get("success"):
                result = AgentResult(
                    content=f"[续跑复用] {str(cached.get('content', ''))}",
                    agent_name=str(cached.get("agent") or st.agent),
                    success=True,
                )
                prog[st.id] = "done"
                self._emit_progress(
                    {"event": "subtask_done", "role": self.display_name,
                     "subtask": st.id, "success": True, "resumed": True}
                )
                return result

        prompt = self._subtask_prompt(st, task, results)
        agent = (
            instance if instance is not None
            else self._resolve_sub_agent(st.agent, st.specialist)
        )
        # N41：worker/实例实际执行一次 → 更新最近使用/次数（扩缩容依据；
        # 常驻 agent/specialist 非池成员，_touch_agent 自动忽略）
        self._touch_agent(agent)
        retries = _env_int("CHUAN_SUBTASK_RETRIES", 1)
        max_attempts = 1 + max(retries, 0)

        result = AgentResult(content="", agent_name=st.agent, success=False)
        attempts = 0
        for attempt in range(max_attempts):
            attempts = attempt + 1
            if attempt > 0:
                self._emit_progress(
                    {"event": "subtask_retry", "role": self.display_name,
                     "subtask": st.id, "attempt": attempt + 1}
                )
                # P1 监督者：重试前查死胡同 → redirect（换 agent / 注入新思路 / 中止）
                decision = self._check_dead_end(
                    session_id, st.id, max_attempts - attempt - 1, agent
                )
                if decision is not None:
                    if decision.is_abort:
                        result = AgentResult(
                            content=f"监督者中止：{decision.reason}",
                            agent_name=st.agent,
                            success=False,
                        )
                        self._record_step(
                            session_id, st.id, attempt,
                            getattr(agent, "name", st.agent),
                            result.success, result.content, hint=decision.reason,
                        )
                        break  # 不再空耗重试
                    if decision.kind == "switch_agent" and decision.target_agent:
                        switched = self.pool.get(decision.target_agent)
                        if switched is not None:
                            agent = switched
                    if decision.hint:
                        prompt = f"{decision.hint}\n\n{prompt}"
            try:
                result = await agent.run(
                    prompt,
                    context={**self.context,
                             "thread_id": f"{session_id}:plan:{st.id}:a{attempt}",
                             "__on_progress__": self._emit_progress},
                )
            except Exception as exc:  # noqa: BLE001 - 单个子任务失败不阻断
                result = AgentResult(
                    content=f"执行出错: {exc}", agent_name=st.agent, success=False
                )

            # 确定性退化检测：免费模型常无视输出约束，把工具调用文本或
            # 原始 JSON 当回复（提示词管不住，必须硬检测兜底）
            if result.success and self._is_degenerate(result.content):
                result = AgentResult(
                    content="子任务未能生成有效结论（模型输出了工具调用原文而非答复），"
                            "请换个问法或重试",
                    agent_name=st.agent,
                    success=False,
                )

            # 监督者：这一步的轨迹记录（供死胡同检测与面板展示）
            self._record_step(
                session_id, st.id, attempt, getattr(agent, "name", st.agent),
                result.success, result.content,
            )
            if result.success:
                break
        else:
            # 尝试耗尽仍未成功 → 监督者最后判定一次，把死胡同记进面板
            self._check_dead_end(
                session_id, st.id, 0, agent, record_note_only=True
            )

        prog[st.id] = "done" if result.success else "failed"
        writer = self._state_writers.get(session_id)
        if writer is not None:
            writer.update(
                st.id, prog[st.id],
                attempts=attempts, summary=str(result.content),
            )
        self._emit_progress(
            {"event": "subtask_done", "role": self.display_name,
             "subtask": st.id, "success": result.success}
        )
        # N35 断点续跑：子任务完成即存结果（旁路，供打断后 /resume 复用）
        if self._resume_store is not None:
            try:
                self._resume_store.save_result(
                    session_id, st.id,
                    success=result.success,
                    content=str(result.content),
                    agent=str(result.agent_name),
                )
            except Exception:  # noqa: BLE001 - 缓存失败不影响执行
                pass
        return result

    # 整段就是一次工具调用文本：list_dir(".") / bash("ls -la") 等
    _TOOL_CALL_RE = re.compile(r"^\s*[a-zA-Z_][a-zA-Z0-9_]*\(.*\)\s*$", re.DOTALL)

    @staticmethod
    def _is_degenerate(content: str) -> bool:
        """检测退化输出：空 / 整段工具调用文本 / 原始工具 JSON 返回。

        正常长回复（含解释和结论）不会整段命中这些模式，误伤面小。
        """
        text = (content or "").strip()
        if not text:
            return True
        # 整段是一次工具调用文本（非代码块、非长文）
        if len(text) < 200 and PersonaRole._TOOL_CALL_RE.match(text):
            return True
        # 原始 MCP/工具 JSON 返回壳（return_code/return_data 风格）
        if text.startswith("{") and (
            '"return_code"' in text or '"return_data"' in text
        ):
            return True
        return False

    def _resolve_sub_agent(self, agent_name: str, specialist: str = "") -> Any:
        """子任务 → agent。

        优先级：specialist（spawn 临时专家）> 岗位持有的实例（N38 spawn_agent
        扩容的，按实例 id 直接用）> 常驻池指名 > auto/builtin/未知名 → 默认内置。
        specialist 临时 agent 按人设缓存复用（重复 spawn 浪费）。
        """
        if specialist:
            if specialist not in self._specialists:
                model = self._resolve_planner()
                spawn = getattr(self.pool, "spawn_builtin", None)
                if model is not None and callable(spawn):
                    self._specialists[specialist] = spawn(
                        model, system_prompt=specialist,
                        name=f"{self.name}:{specialist[:16]}",
                    )
                else:  # 无法 spawn（无模型/无能力）→ 默认内置兜底
                    return self._ensure_default_agent()
            return self._specialists[specialist]
        # N38：子任务显式指定岗位持有的实例（如 "writer"/"analyst"）→ 直接用
        role_instance = self._agents.get(agent_name)
        if role_instance is not None:
            return role_instance
        if agent_name not in ("auto", "builtin"):
            agent = self.pool.get(agent_name)
            if agent is not None:
                return agent
        return self._ensure_default_agent()

    # ------------------------------------------------------------------ #
    # P1 监督者旁路（SupervisorMonitor）：轨迹记录 + 死胡同 redirect
    # ------------------------------------------------------------------ #
    def _begin_trace(self, session_id: str) -> None:
        """开启本次 dispatch 的执行轨迹（旁路，失败不影响执行）。"""
        if self._monitor is None:
            return
        try:
            self._monitor.start_trace(self.name, session_id)
        except Exception:  # noqa: BLE001, S110 - 监控是旁路
            pass

    def _finish_trace(self, session_id: str) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.finish_trace(session_id)
        except Exception:  # noqa: BLE001, S110
            pass

    def _record_step(
        self,
        session_id: str,
        step: str,
        attempt: int,
        agent: str,
        success: bool,
        content: str,
        hint: str = "",
    ) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.record_step(
                session_id, step, attempt=attempt, agent=agent,
                success=success, content=content, duration=0.0, hint=hint,
            )
        except Exception:  # noqa: BLE001, S110
            pass

    def _check_dead_end(
        self,
        session_id: str,
        step: str,
        retries_left: int,
        agent: Any,
        *,
        record_note_only: bool = False,
    ):
        """重试前查询监督者：该步历史尝试是否死胡同。

        Returns:
            RedirectDecision | None；record_note_only 时只记面板标注，不应用。
        """
        if self._monitor is None:
            return None
        try:
            available = self.pool.list_resident()
        except Exception:  # noqa: BLE001
            available = []
        try:
            decision = self._monitor.check_dead_end(
                session_id, step,
                retries_left=retries_left,
                current_agent=getattr(agent, "name", str(step)),
                available=available,
            )
        except Exception:  # noqa: BLE001 - 监控误判不阻断
            return None
        if decision is None:
            return None
        if not record_note_only:
            try:
                self._monitor.record_redirect(session_id, step, decision)
            except Exception:  # noqa: BLE001, S110
                pass
        return decision

    def _emit_progress(self, event: dict[str, Any]) -> None:
        """进度上报；回调异常不阻断执行。"""
        if self.on_progress is None:
            return
        try:
            self.on_progress(event)
        except Exception:  # noqa: BLE001, S110 - 进度上报是旁路
            pass

    # ------------------------------------------------------------------ #
    # 阶段2：结果汇总
    # ------------------------------------------------------------------ #
    def _summarize(
        self, task: str, plan: list[SubTask], results: dict[str, AgentResult]
    ) -> str:
        """确定性汇总（不走 LLM）：单结果直出，多结果分节标注成败。"""
        if len(plan) == 1:
            return results[plan[0].id].content
        sections: list[str] = []
        for st in plan:
            result = results.get(st.id)
            if result is None:
                continue
            mark = "" if result.success else "（失败）"
            sections.append(f"### {st.description}{mark}\n{result.content}")
        return "\n\n".join(sections)

    # ------------------------------------------------------------------ #
    # N37 岗位化 1:N —— 岗位持有的 agent 实例池
    # ------------------------------------------------------------------ #
    def _ensure_default_agent(self) -> BuiltinAgent:
        """默认 agent 实例懒加载（instance_id="default"，向后兼容 1:1）。

        第一次调用时创建并纳入岗位持有的 agent 池，后续复用。
        """
        default = self._agents.get("default")
        if default is None:
            default = self.pool.get_builtin_agent(
                self.name, checkpointer=self._checkpointer
            )
            self._agents["default"] = default
            # N41：默认实例也纳入用量统计（扩缩容观测；永不回收）
            self._init_instance_stat("default")
        return default

    def spawn_agent(
        self,
        instance_id: str,
        system_prompt: str = "",
        tools: list | None = None,
        model: Any = None,
        *,
        config: RoleAgentConfig | None = None,
        checkpointer: Any = None,
    ) -> Any:
        """岗位显式扩容：创建并持有第 N 个 agent 实例（1:1 → 1:N）。

        让一个岗位可以管理多个 agent（不同人设/工具/模型），供并行子任务、
        多会话或按任务复杂度选实例。默认实例（"default"）始终保留；
        实例 id 已存在时直接复用（幂等）。

        N39 按实例配置：``config``（RoleAgentConfig）提供结构化的工具/模型/
        系统提示词/会话存档（记忆）；旧关键字参数（system_prompt/tools/model/
        checkpointer）可在 config 之上覆盖。

        Args:
            instance_id: 实例标识（岗位内唯一，如 "writer" / "analyst"）
            system_prompt: 该实例的系统提示词；留空用岗位人设描述
            tools: 工具子集；None 用 ToolRegistry 全集（ADR-009）
            model: 聊天模型；None 用岗位 brain 的模型
            config: N39 结构化按实例配置（工具/模型/提示词/记忆）
            checkpointer: N39 实例级会话存档；None 用岗位共享

        Returns:
            新实例（无法扩容时回退默认实例，不抛错）
        """
        existing = self._agents.get(instance_id)
        if existing is not None:
            return existing
        # N39：config 为基础，旧关键字参数可覆盖其默认值
        if config is not None:
            tools = tools if tools is not None else config.tools
            model = model if model is not None else config.model
            system_prompt = system_prompt or config.system_prompt
            checkpointer = (
                checkpointer if checkpointer is not None else config.checkpointer
            )
        if model is None:
            model = self._resolve_planner()
        spawn = getattr(self.pool, "spawn_builtin", None)
        if model is None or not callable(spawn):
            # 无法扩容（无模型/无能力）→ 默认实例兜底，不阻断
            return self._ensure_default_agent()
        system_prompt = system_prompt or self.description
        agent = spawn(
            model, tools=tools,
            system_prompt=system_prompt,
            name=f"{self.name}:{instance_id[:24]}",
            checkpointer=checkpointer,
        )
        self._agents[instance_id] = agent
        # N39：记录实例配置（工具/模型/提示词/记忆），供检视审计
        self._agent_configs[instance_id] = RoleAgentConfig(
            tools=tools, model=model,
            system_prompt=system_prompt, checkpointer=checkpointer,
        )
        # N41：动态池用量统计起点
        self._init_instance_stat(instance_id)
        return agent

    def agent_count(self) -> int:
        """岗位当前持有的 agent 实例总数（默认 + 扩容，1 起步）。"""
        return len(self._agents)

    def list_agents(self) -> list[str]:
        """列出岗位持有的 agent 实例 id（含默认实例 "default"）。"""
        return list(self._agents)

    # ------------------------------------------------------------------ #
    # N38 岗位化 1:N —— 并行子任务独立 worker 实例（1:N 默认启用）
    # ------------------------------------------------------------------ #
    def _assign_wave_instances(self, ready: list[SubTask]) -> dict[str, Any]:
        """给一波并行子任务分配独立 worker 实例（N38 1:N 默认启用）。

        仅当本波有 ≥2 个 auto/builtin 且无 specialist 的并行子任务时生效：
        各配一个独立 worker 实例（按需创建，上限 ``CHUAN_PARALLEL_WORKERS``），
        避免所有并行子任务挤在同一默认实例上；单任务/指定实例/常驻 agent
        保持原调度（向后兼容）。
        """
        parallel = [
            st for st in ready
            if st.agent in ("auto", "builtin") and not st.specialist
        ]
        if len(parallel) < 2:
            return {}
        # N41：扩容上限 = 池配置 max（若开启动态池），否则 CHUAN_PARALLEL_WORKERS
        cap = max(_env_int("CHUAN_PARALLEL_WORKERS", 3), 1)
        if self._pool_config is not None:
            cap = max(self._pool_config.max_instances, 1)
        assignments: dict[str, Any] = {}
        for i, st in enumerate(parallel):
            worker_id = f"worker{i % cap}"
            assignments[st.id] = self._ensure_worker_instance(worker_id)
        return assignments

    def _ensure_worker_instance(self, instance_id: str) -> Any:
        """取/建一个岗位 worker 实例（同 persona 的独立图实例）。

        worker 是岗位 1:N 的并行执行者：与默认实例同人设同工具，但用
        ``force_rebirth`` 拿到独立图（避免并行子任务共享同一图）。已存在则
        复用；池无 spawn 能力或创建失败 → 回退默认实例（旁路，不抛错）。

        N39 按实例配置：``self._worker_config`` 非空时，worker 用其工具子集/
        模型/系统提示词/实例级会话存档（记忆）覆盖 persona 默认——让并行
        worker 也能按实例定制，而不只是全人设复制。
        """
        existing = self._agents.get(instance_id)
        if existing is not None:
            return existing
        spawn = getattr(self.pool, "spawn_builtin_instance", None)
        if not callable(spawn):
            return self._ensure_default_agent()
        cfg = self._worker_config
        try:
            agent = spawn(
                self.name,
                checkpointer=(
                    cfg.checkpointer if cfg is not None else None
                ) or self._checkpointer,
                model=cfg.model if cfg is not None else None,
                tools=cfg.tools if cfg is not None else None,
                system_prompt=(
                    cfg.system_prompt if cfg is not None else None
                ) or None,
            )
        except Exception:  # noqa: BLE001 - worker 创建失败回退默认实例
            return self._ensure_default_agent()
        self._agents[instance_id] = agent
        if cfg is not None:
            self._agent_configs[instance_id] = cfg
        # N41：worker 实例创建即入用量统计（扩缩容依据）
        self._init_instance_stat(instance_id)
        return agent

    # ------------------------------------------------------------------ #
    # N40 按任务复杂度选实例（config.yaml role_instances 声明式配置）
    # ------------------------------------------------------------------ #
    # 重型任务标记：命中即判 heavy（用更强模型/编码工具的声明式实例）
    _HEAVY_MARKERS = (
        "代码", "编程", "写代码", "开发", "重构", "排查", "调试",
        "编译", "部署", "修复", "实现", "脚本", "编码",
    )

    def _classify_complexity(self, task: str) -> str:
        """确定性任务复杂度分级：simple / medium / heavy（N40）。

        - heavy：命中重型标记（代码/开发/调试…）
        - medium：会走规划（复合任务，有步骤词/足够长）
        - simple：其余（短问答/单步操作）
        纯规则、无 LLM 调用，保证可测与零额外开销。
        """
        if any(m in task for m in self._HEAVY_MARKERS):
            return "heavy"
        if self._should_plan(task):
            return "medium"
        return "simple"

    def _resolve_tier_instance(self, tier: str) -> Any:
        """按任务复杂度档位取执行实例（N40 声明式配置）。

        用 ``_instance_config`` 的 tier 映射（角色可覆盖）找到实例 id →
        按其声明配置创建/复用；未配置声明式方案 / 实例缺失 / 创建失败
        → 回退默认实例（旁路，不抛错，保持 1:1 行为）。
        """
        if self._instance_config is None:
            return self._ensure_default_agent()
        tiers = self._instance_config.tier_for(self.name)
        instance_id = tiers.get(tier) or "default"
        if instance_id == "default":
            return self._ensure_default_agent()
        return self._ensure_configured_instance(instance_id)

    def _ensure_configured_instance(self, instance_id: str) -> Any:
        """取/建一个由 ``role_instances`` 声明的实例（N40）。

        与 worker 一样用 ``spawn_builtin_instance``（独立图），但按声明的
        RoleAgentConfig 覆盖工具/模型/系统提示词/会话存档。已存在则复用；
        池无能力或创建失败 → 回退默认实例（旁路）。
        """
        existing = self._agents.get(instance_id)
        if existing is not None:
            return existing
        cfg = (self._instance_config.instances or {}).get(instance_id)
        if cfg is None:
            return self._ensure_default_agent()
        spawn = getattr(self.pool, "spawn_builtin_instance", None)
        if not callable(spawn):
            return self._ensure_default_agent()
        try:
            agent = spawn(
                self.name,
                checkpointer=cfg.checkpointer or self._checkpointer,
                model=cfg.model,
                tools=cfg.tools,
                system_prompt=cfg.system_prompt or None,
            )
        except Exception:  # noqa: BLE001 - 实例创建失败回退默认
            return self._ensure_default_agent()
        self._agents[instance_id] = agent
        self._agent_configs[instance_id] = cfg
        self._init_instance_stat(instance_id)
        return agent

    # ------------------------------------------------------------------ #
    # N41 动态实例池：用量跟踪 + 自动扩缩容
    # ------------------------------------------------------------------ #
    def _init_instance_stat(self, instance_id: str) -> None:
        """记录实例创建时间（N41 用量统计起点，创建即视为在用）。"""
        now = time.monotonic()
        self._instance_stats[instance_id] = _InstanceStat(
            created_at=now, last_used_at=now
        )

    def _touch_agent(self, agent: Any) -> None:
        """实例实际执行一次 → 更新最近使用时间与次数（N41）。

        只统计岗位持有的实例（``_agents`` 里的 worker/声明式/扩容实例）；
        常驻 command agent/specialist 不是池成员，忽略。O(池大小) 开销可忽略。
        """
        for instance_id, inst in self._agents.items():
            if inst is agent:
                stat = self._instance_stats.setdefault(
                    instance_id, _InstanceStat()
                )
                stat.last_used_at = time.monotonic()
                stat.uses += 1
                return

    def _maybe_reclaim_idle(self) -> None:
        """开工前自动缩容：回收空闲超 TTL 的实例（仅开启动态池时，旁路）。

        每次 dispatch 开工调用一次：把上次任务闲置超时的 worker/实例回收掉，
        释放资源；再次需要时按需重建（扩容闭环）。回收数上报进度事件。
        """
        if self._pool_config is None:
            return
        reclaimed = self.reclaim_idle()
        if reclaimed:
            self._emit_progress(
                {"event": "pool_reclaim", "role": self.display_name,
                 "reclaimed": reclaimed, "size": len(self._agents)}
            )

    def reclaim_idle(
        self,
        idle_ttl: float | None = None,
        keep_min: int | None = None,
    ) -> int:
        """自动缩容：回收空闲超过 TTL 的非默认实例，保留至少 keep_min 个。

        默认实例是岗位身份，永不回收；被回收的实例再次需要时按需重建
        （扩容闭环）。TTL/下限缺省取池配置，未配置用默认值（300s/1 个）。
        返回回收的实例数。
        """
        ttl = idle_ttl
        if ttl is None:
            ttl = (
                self._pool_config.idle_ttl
                if self._pool_config is not None else 300.0
            )
        min_keep = keep_min
        if min_keep is None:
            min_keep = (
                self._pool_config.min_instances
                if self._pool_config is not None else 1
            )
        now = time.monotonic()
        # 非默认实例按「最近使用」升序（最久未用优先回收）
        candidates = sorted(
            (
                iid for iid in self._agents if iid != "default"
            ),
            key=lambda iid: (
                self._instance_stats[iid].last_used_at
                if iid in self._instance_stats else 0.0
            ),
        )
        reclaimable = max(len(candidates) - max(min_keep, 0), 0)
        reclaimed = 0
        for iid in candidates[:reclaimable]:
            stat = self._instance_stats.get(iid)
            if stat is None or (now - stat.last_used_at) < ttl:
                continue
            del self._agents[iid]
            self._agent_configs.pop(iid, None)
            self._instance_stats.pop(iid, None)
            reclaimed += 1
        return reclaimed

    def pool_stats(self) -> dict[str, Any]:
        """动态实例池状态（N41 供 TUI/心跳观测）。"""
        now = time.monotonic()
        ttl = (
            self._pool_config.idle_ttl
            if self._pool_config is not None else 300.0
        )
        return {
            "size": len(self._agents),
            "min": (
                self._pool_config.min_instances
                if self._pool_config is not None else None
            ),
            "max": (
                self._pool_config.max_instances
                if self._pool_config is not None else None
            ),
            "idle": sum(
                1 for iid in self._agents if iid != "default"
                and (now - self._instance_stats[iid].last_used_at) >= ttl
            ),
            "uses": {
                iid: stat.uses
                for iid, stat in self._instance_stats.items()
            },
        }

    # ------------------------------------------------------------------ #
    # 会话级进度视图（1:N：同一岗位并行服务多会话互不串扰）
    # ------------------------------------------------------------------ #
    def _session_progress_view(self, session_id: str) -> dict[str, str]:
        """取某会话的进度视图（不存在则创建）；同步 role 级最近会话视图。

        role 级 ``self.progress`` 指向最近访问的会话进度，保持 1:1 时代的
        直接读法（``role.progress["s1"]``）与 TUI/测试向后兼容。
        """
        prog = self._session_progress.setdefault(session_id, {})
        self.progress = prog
        return prog

    # ------------------------------------------------------------------ #
    # 显式 agent 检测
    # ------------------------------------------------------------------ #
    def _detect_explicit_agent(self, task: str) -> str | None:
        """检测用户是否显式指定了外部 agent。

        例如 "用 pi 帮我写代码" → "pi"
        """
        task_lower = task.lower()
        for keyword, agent_name in self._EXPLICIT_AGENT_MAP:
            if keyword in task_lower:
                return agent_name
        return None

    def _strip_explicit_prefix(self, task: str, agent_name: str) -> str:
        """去掉 task 中的显式指定前缀。

        例如 "用pi写个hello world" → "写个hello world"
             "用 pi 帮我写代码" → "帮我写代码"
        """
        task_lower = task.lower()
        for keyword, name in self._EXPLICIT_AGENT_MAP:
            if name == agent_name and keyword in task_lower:
                idx = task_lower.find(keyword)
                return task[idx + len(keyword):].strip()
        return task

    # ------------------------------------------------------------------ #
    # 结果包装
    # ------------------------------------------------------------------ #
    def _wrap_result(self, result: AgentResult, task: str = "") -> str:
        """人设包装：加 display_name 前缀，并触发 GEPA 自改进与 howto/skill 自动沉淀。"""
        self._maybe_self_improve(task, result)
        self._maybe_distill_howto(task, result)
        self._maybe_create_skill(task, result)
        if result.success:
            return f"[{self.display_name}] {result.content}"
        return f"[{self.display_name}] 执行失败：{result.content}"

    def _maybe_self_improve(self, task: str, result: AgentResult) -> None:
        """任务完成后自动评估并沉淀经验到角色 MEMORY.md（N20 GEPA）。

        仅目录格式角色（ADR-013）生效；非目录角色 run_gepa 直接返回 False。
        任何异常静默吞掉——自改进是旁路，绝不影响主流程答复。
        """
        from chuan.self_improve.gepa import run_gepa

        try:
            run_gepa(self.persona, task, result.content, result.success)
        except Exception:  # noqa: BLE001 - 自改进失败不阻断答复
            pass

    # ------------------------------------------------------------------ #
    # N26 L3 从做到造 —— 任务开工前自动复用「怎么做」知识原子
    # ------------------------------------------------------------------ #
    def _maybe_inject_howto(self, task: str) -> str:
        """任务文本里注入强命中的「参考做法」（确定性、旁路）。

        命中分低于阈值视为噪声不注入；无 memory / 异常一律返回原任务。
        """
        if self._memory is None or not task.strip():
            return task
        try:
            if self._howto_store is None:
                from chuan.howto import HowToStore

                self._howto_store = HowToStore(self._memory)
            ref = self._howto_store.suggest(task)
        except Exception:  # noqa: BLE001 - 复用注入失败不阻断任务
            return task
        if not ref:
            return task
        return f"{ref}\n\n{task}"

    # ------------------------------------------------------------------ #
    # N27 L3 从做到造 —— 任务收尾自动提炼「怎么做」知识原子（staging 待确认）
    # ------------------------------------------------------------------ #
    def _maybe_distill_howto(self, task: str, result: AgentResult) -> None:
        """任务成功收尾后自动提炼候选做法原子入 staging 队列（旁路）。

        只处理成功任务；候选经人工确认（/howto approve）后才落入 howto
        知识库。无 memory / 异常一律静默跳过，绝不阻断答复。
        """
        if self._memory is None or not result.success:
            return
        try:
            if self._distiller is None:
                from chuan.howto_distill import HowToDistiller

                self._distiller = HowToDistiller(self._memory)
            self._distiller.maybe_distill(
                task, result.content,
                success=result.success, source=f"role:{self.name}",
            )
        except Exception:  # noqa: BLE001 - 自动沉淀失败不阻断答复
            pass

    # ------------------------------------------------------------------ #
    # N30 L3 从做到造 —— 任务开工前自动复用已注册技能（触发词命中）
    # ------------------------------------------------------------------ #
    def _inject_reference(self, task: str) -> str:
        """开工前注入参考做法：**已注册技能（触发词精确命中）优先**，否则知识原子兜底。

        技能是比知识原子更强的复用单元（带触发关键词）；两者都不命中时返回原任务。
        """
        ref = self._maybe_inject_skill(task)
        if ref != task:
            return ref
        return self._maybe_inject_howto(task)

    def _maybe_inject_skill(self, task: str) -> str:
        """任务文本命中已注册技能的触发关键词时，注入其可复用做法（旁路）。

        命中则返回带「【参考技能】」前缀的任务文本，否则原任务不变。
        每次现读 ``skills/`` 目录（廉价），保证同一会话内新确认的技能即时生效。
        """
        if self._memory is None or not task.strip():
            return task
        try:
            from chuan.adapters.skill_loader import SkillRegistry

            skill = SkillRegistry().find_prompt_skill(task)
        except Exception:  # noqa: BLE001 - 复用注入失败不阻断任务
            return task
        if skill is None:
            return task
        return f"{skill.render_prompt()}\n\n{task}"

    # ------------------------------------------------------------------ #
    # N30 L3 从做到造 —— 任务收尾自动提炼「可注册技能」（staging 待确认）
    # ------------------------------------------------------------------ #
    def _maybe_create_skill(self, task: str, result: AgentResult) -> None:
        """任务成功收尾后自动提炼候选技能入 staging 队列（旁路）。

        只处理成功任务；候选经人工确认（/skill approve）后才写入 skills/*.yaml
        并注册进 SkillRegistry。无 memory / 异常一律静默跳过，绝不阻断答复。
        """
        if self._memory is None or not result.success:
            return
        try:
            if self._skill_creator is None:
                from chuan.skill_creator import SkillCreator

                self._skill_creator = SkillCreator(self._memory)
            self._skill_creator.maybe_create(
                task, result.content,
                success=result.success, source=f"role:{self.name}",
            )
        except Exception:  # noqa: BLE001 - 自动技能创建失败不阻断答复
            pass
