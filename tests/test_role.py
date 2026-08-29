"""岗位阶段2/3测试：任务拆分 + 分波并行执行 + specialist spawn + 进度跟踪。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from chuan.agent_pool import AgentPool
from chuan.agents.base import AgentResult
from chuan.role import Department, PlanError, RoleAgentConfig, RolePoolConfig, SubTask


# ── 测试替身 ─────────────────────────────────────────


class _Persona:
    name = "researcher"
    display_name = "调研员"
    description = "测试岗位"


class FakeAgent:
    """记录调用并把 prompt 原样回显的假 agent。"""

    def __init__(self, name: str = "fake", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[str, dict | None]] = []

    async def run(self, task: str, context: dict | None = None) -> AgentResult:
        self.calls.append((task, context))
        await asyncio.sleep(0)  # 让出控制权，使并行协程得以交错
        if self.fail:
            return AgentResult(content=f"出错:{task}", agent_name=self.name, success=False)
        return AgentResult(content=f"结果:{task}", agent_name=self.name)


class FakePool:
    """假 AgentPool：常驻池 + 默认内置 agent + 可选规划模型。"""

    def __init__(self, resident: dict[str, FakeAgent] | None = None, model: Any = None):
        self._resident = resident or {}
        self._model = model
        self.default = FakeAgent("builtin")
        self.builtin_births = 0
        self.spawn_calls: list[dict] = []

    def get(self, name: str) -> FakeAgent | None:
        return self._resident.get(name)

    def list_resident(self) -> list[str]:
        return list(self._resident)

    def get_model(self, name: str) -> Any:
        return self._model

    def get_builtin_agent(self, name: str, checkpointer: Any = None) -> FakeAgent:
        self.builtin_births += 1
        return self.default

    def spawn_builtin(self, model: Any, tools: list | None = None,
                      system_prompt: str = "", name: str = "",
                      checkpointer: Any = None) -> FakeAgent:
        self.spawn_calls.append(
            {"model": model, "prompt": system_prompt, "name": name,
             "tools": tools, "checkpointer": checkpointer}
        )
        return FakeAgent(name=f"spawned")


class FakeModel:
    """假规划模型：返回固定内容，记录 prompt。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


def _role(
    model: FakeModel | None = None, resident: dict | None = None, teams_root: str | None = None
) -> tuple[Department, FakePool]:
    pool = FakePool(resident=resident, model=model)
    return (
        Department(_Persona(), pool, planner_model=model, teams_root=teams_root),
        pool,
    )


_PLAN_JSON = (
    '{"subtasks": ['
    '{"id": "s1", "description": "查武汉天气", "agent": "auto", "depends_on": []},'
    '{"id": "s2", "description": "给穿搭建议", "agent": "auto", "depends_on": ["s1"]}'
    "]}"
)


# ── 规划门槛 ─────────────────────────────────────────


async def test_should_plan_gate() -> None:
    role, _ = _role()
    assert role._should_plan("先查天气，然后总结") is True  # 步骤词
    assert role._should_plan("x" * 24) is True  # 长任务
    assert role._should_plan("今天天气") is False  # 简单短任务


async def test_should_plan_env_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUAN_PLAN", "0")
    model = FakeModel(_PLAN_JSON)
    role, _ = _role(model)
    reply = await role.dispatch("先查天气，然后写总结")
    assert len(model.prompts) == 0  # 规划被关闭，模型零调用
    assert "结果:先查天气" in reply  # 走单 agent


# ── _plan：解析与校验 ────────────────────────────────


async def test_plan_parses_valid_json_with_deps() -> None:
    role, _ = _role(FakeModel(_PLAN_JSON))
    plan = await role._plan("先查天气再给建议")
    assert [st.id for st in plan] == ["s1", "s2"]
    assert plan[1].depends_on == ["s1"]


async def test_plan_tolerates_markdown_fence() -> None:
    role, _ = _role(FakeModel(f"好的，规划如下：\n```json\n{_PLAN_JSON}\n```\n以上。"))
    plan = await role._plan("先查天气再给建议")
    assert len(plan) == 2


@pytest.mark.parametrize(
    "content",
    [
        "我认为不需要拆分",  # 无 JSON
        '{"subtasks": "not-a-list"}',  # 结构错
        '{"subtasks": [{"id": "s1", "description": ""}]}',  # 空 description
        '{"subtasks": [{"id": "s1", "description": "a"}, {"id": "s1", "description": "b"}]}',  # id 重复
        '{"subtasks": [{"id": "s1", "description": "a", "depends_on": ["s9"]}]}',  # 依赖不存在
        '{"subtasks": [{"id": "s1", "description": "a", "depends_on": ["s1"]}]}',  # 依赖自己
        '{"subtasks": [{"id": "a", "description": "x", "depends_on": ["b"]},'
        '{"id": "b", "description": "y", "depends_on": ["a"]}]}',  # 成环
    ],
)
async def test_plan_invalid_output_raises(content: str) -> None:
    role, _ = _role(FakeModel(content))
    with pytest.raises(PlanError):
        await role._plan("先查天气再给建议")


async def test_plan_model_failure_raises() -> None:
    class BoomModel:
        async def ainvoke(self, prompt: str) -> SimpleNamespace:
            raise RuntimeError("网络断开")

    role, _ = _role(BoomModel())
    with pytest.raises(PlanError):
        await role._plan("先查天气再给建议")


async def test_plan_no_model_raises() -> None:
    role, _ = _role(None)
    with pytest.raises(PlanError):
        await role._plan("先查天气再给建议")


# ── _topo_order / _execute：串行执行 + 依赖注入 ─────


def test_topo_order_respects_deps_and_stability() -> None:
    plan = [
        SubTask(id="s1", description="独立A"),
        SubTask(id="s2", description="独立B"),
        SubTask(id="s3", description="依赖AB", depends_on=["s1", "s2"]),
    ]
    order = Department._topo_order(plan)
    assert [st.id for st in order] == ["s1", "s2", "s3"]  # 无依赖保序


async def test_execute_serial_with_dependency_injection() -> None:
    role, pool = _role()
    plan = [
        SubTask(id="s1", description="查天气"),
        SubTask(id="s2", description="给建议", depends_on=["s1"]),
    ]
    results = await role._execute(plan, session_id="sess1")
    assert len(pool.default.calls) == 2
    # s2 的提示注入了 s1 的结果
    s2_prompt = pool.default.calls[1][0]
    assert "【前置子任务结果】" in s2_prompt
    assert "查天气" in s2_prompt
    # 每个子任务独立 thread（带 attempt 后缀，重试不共用历史）
    t1 = pool.default.calls[0][1]["thread_id"]
    t2 = pool.default.calls[1][1]["thread_id"]
    assert t1 == "sess1:plan:s1:a0" and t2 == "sess1:plan:s2:a0"
    assert set(results) == {"s1", "s2"}


async def test_execute_failure_does_not_block_independents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHUAN_SUBTASK_RETRIES", "0")  # 本测试关注失败语义，关重试
    role, pool = _role()
    pool.default = FakeAgent("builtin", fail=True)
    plan = [
        SubTask(id="s1", description="会失败"),
        SubTask(id="s2", description="无依赖照常跑"),
        SubTask(id="s3", description="依赖s1", depends_on=["s1"]),
    ]
    results = await role._execute(plan)
    assert len(pool.default.calls) == 3  # 全部执行
    assert results["s1"].success is False
    assert results["s2"].success is False  # fail agent 全失败，但没被阻断
    # 失败结果照样注入后继提示
    assert "【s1 的结果】" in pool.default.calls[2][0]


async def test_execute_resolves_named_resident_agent() -> None:
    pi = FakeAgent("pi")
    role, _ = _role(resident={"pi": pi})
    plan = [SubTask(id="s1", description="写脚本", agent="pi")]
    await role._execute(plan)
    assert len(pi.calls) == 1
    assert "写脚本" in pi.calls[0][0]
    # 未知 agent 名回退默认内置
    plan2 = [SubTask(id="s1", description="x", agent="不存在的")]
    results = await role._execute(plan2)
    assert results["s1"].agent_name == "builtin"


async def test_subtask_prompt_injects_context_and_output_rules() -> None:
    """子任务提示必须带总任务背景 + 输出要求（防免费模型贴工具原文）。"""
    role, _ = _role()
    plan = [
        SubTask(id="s1", description="查天气"),
        SubTask(id="s2", description="给建议", depends_on=["s1"]),
    ]
    await role._execute(plan, task="先查天气再给建议")
    s1_prompt, s2_prompt = pool_prompts(role)
    # 总任务背景注入
    assert "【总任务】" in s1_prompt and "先查天气再给建议" in s1_prompt
    # 输出约束注入
    assert "【输出要求】" in s1_prompt and "禁止输出工具调用代码" in s1_prompt
    # 依赖注入 + 输出约束同时存在
    assert "【前置子任务结果】" in s2_prompt
    assert "【输出要求】" in s2_prompt


def pool_prompts(role: Department) -> tuple[str, str]:
    pool = role.pool  # type: ignore[attr-defined]
    return pool.default.calls[0][0], pool.default.calls[1][0]


# ── 退化输出确定性检测 ───────────────────────────────


def test_is_degenerate_tool_call_text() -> None:
    """整段工具调用文本（免费模型常见退化）必须被判退化。"""
    assert Department._is_degenerate('list_dir(".")') is True
    assert Department._is_degenerate('bash("ls -la")') is True
    assert Department._is_degenerate('read_file("README.md")') is True
    assert Department._is_degenerate("") is True
    assert Department._is_degenerate("   ") is True


def test_is_degenerate_raw_json_shell() -> None:
    """原始 MCP JSON 返回壳必须被判退化。"""
    assert Department._is_degenerate(
        '{"return_code": 0, "return_message": "", "return_data": ["chuan", "docs"]}'
    ) is True


def test_is_degenerate_normal_reply_is_kept() -> None:
    """正常回复不误伤：长解释、含工具名的正文、markdown 代码块。"""
    # 正常长答复（含结论）
    assert Department._is_degenerate(
        "根据调研，XX市场主要有三家竞品：A 公司主打低端、B 公司……（以下省略两百字）"
    ) is False
    # 正文提到工具名但不是纯工具调用
    assert Department._is_degenerate(
        "我先调用了 list_dir 查看了目录，发现没有相关资料，建议补充信息。"
    ) is False
    # 普通短答复
    assert Department._is_degenerate("今天武汉晴，26 度。") is False
    # 代码块里的工具调用（围栏包裹，非整段裸调用）
    assert Department._is_degenerate('```\nlist_dir(".")\n```') is False


async def test_execute_marks_degenerate_result_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子任务结果退化为工具调用文本 → 标记失败并替换 content，不进汇总正文。"""
    monkeypatch.setenv("CHUAN_SUBTASK_RETRIES", "0")  # 退化测试不关心重试
    role, pool = _role()
    pool.default = FakeAgent("builtin")

    async def degenerate_run(task, context=None):
        return AgentResult(content='list_dir(".")', agent_name="builtin")

    pool.default.run = degenerate_run  # type: ignore[method-assign]
    plan = [SubTask(id="s1", description="调研竞品")]
    results = await role._execute(plan)
    assert results["s1"].success is False
    assert "工具调用原文" in results["s1"].content
    assert role.progress["s1"] == "failed"


# ── attempt 重试（借鉴 dsh-agent-teams） ─────────────


async def test_subtask_retries_on_failure() -> None:
    """偶发失败自动重试：第一次失败第二次成功 → 最终成功。"""
    role, pool = _role()
    pool.default = FakeAgent("builtin")
    calls = {"n": 0}

    async def flaky_run(task, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return AgentResult(content="执行出错: 超时", success=False)
        return AgentResult(content="重试成功的结果", agent_name="builtin")

    pool.default.run = flaky_run  # type: ignore[method-assign]
    events: list[dict] = []
    role.on_progress = events.append

    plan = [SubTask(id="s1", description="查数据")]
    results = await role._execute(plan)
    assert calls["n"] == 2
    assert results["s1"].success is True
    assert results["s1"].content == "重试成功的结果"
    assert role.progress["s1"] == "done"
    # retry 事件已上报
    retries = [e for e in events if e["event"] == "subtask_retry"]
    assert len(retries) == 1 and retries[0]["attempt"] == 2


async def test_subtask_retry_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHUAN_SUBTASK_RETRIES=0 → 关闭重试，失败即终局。"""
    monkeypatch.setenv("CHUAN_SUBTASK_RETRIES", "0")
    role, pool = _role()
    pool.default = FakeAgent("builtin")
    calls = {"n": 0}

    async def always_fail(task, context=None):
        calls["n"] += 1
        return AgentResult(content="执行出错: x", success=False)

    pool.default.run = always_fail  # type: ignore[method-assign]
    plan = [SubTask(id="s1", description="查数据")]
    results = await role._execute(plan)
    assert calls["n"] == 1
    assert results["s1"].success is False


async def test_subtask_retry_exhausted() -> None:
    """重试耗尽 → 最终失败，尝试次数 = 1 + retries。"""
    role, pool = _role()
    pool.default = FakeAgent("builtin")
    calls = {"n": 0}

    async def always_fail(task, context=None):
        calls["n"] += 1
        return AgentResult(content="执行出错: x", success=False)

    pool.default.run = always_fail  # type: ignore[method-assign]
    plan = [SubTask(id="s1", description="查数据")]
    results = await role._execute(plan)
    assert calls["n"] == 2  # 默认 1 次重试
    assert results["s1"].success is False
    assert role.progress["s1"] == "failed"


# ── 团队状态落盘（借鉴 dsh 磁盘真相） ─────────────────


async def test_team_state_written_and_finished(tmp_path) -> None:
    """dispatch 走规划路径 → data/teams/（tmp root）落盘且终态 finished。"""
    role, pool = _role(model=FakeModel(_PLAN_JSON), teams_root=str(tmp_path))
    reply = await role.dispatch("先查武汉天气，然后给穿搭建议")

    import json

    files = list((tmp_path / "teams").glob("*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["role"] == role.display_name
    assert doc["task"] == "先查武汉天气，然后给穿搭建议"
    assert doc["status"] == "finished"
    sts = {st["id"]: st for st in doc["subtasks"]}
    assert sts["s1"]["status"] == "done"
    assert sts["s2"]["status"] == "done"
    assert "查武汉天气" in sts["s1"]["summary"]


def test_team_state_writer_and_load_unfinished(tmp_path) -> None:
    """写入器单测 + load_unfinished 只报 running 的档案。"""
    from chuan.team_state import TeamStateWriter, load_unfinished

    w = TeamStateWriter("研究", "调研任务", "sess-a", root=tmp_path)
    w.init_subtasks([{"id": "s1", "description": "查数据"}])
    w.update("s1", "running")
    # 未完成 → load_unfinished 能看到
    docs = load_unfinished(tmp_path)
    assert len(docs) == 1 and docs[0]["task"] == "调研任务"
    assert docs[0]["subtasks"][0]["status"] == "running"

    w.update("s1", "done", attempts=2, summary="完成")
    w.finish()
    # 已完成 → 不再报
    assert load_unfinished(tmp_path) == []


def test_team_state_writer_bad_session_id(tmp_path) -> None:
    """session_id 含路径注入字符 → 清洗成安全文件名。"""
    from chuan.team_state import TeamStateWriter

    w = TeamStateWriter("研究", "t", "../../evil", root=tmp_path)
    w.init_subtasks([{"id": "s1", "description": "x"}])
    files = list((tmp_path / "teams").glob("*.json"))
    assert len(files) == 1
    assert ".." not in files[0].name


# ── 成员消息直通 ask_role（借鉴 dsh mailbox） ─────────


class FakeRole:
    """总线测试假岗位。"""

    def __init__(self, name: str, display: str, reply: str = "答复") -> None:
        self.name = name
        self.display_name = display
        self.display = display
        self._reply = reply
        self.calls: list[str] = []

    async def dispatch(self, task: str, session_id: str = "default") -> str:
        self.calls.append((task, session_id))
        return f"[{self.display_name}] {self._reply}"


async def test_ask_role_routes_to_target_role() -> None:
    from chuan import team_bus

    housekeeper = FakeRole("housekeeper", "管家", "今天晴 28 度")
    team_bus.register_roles({"housekeeper": housekeeper})
    try:
        result = await team_bus._ask_role_async("管家", "武汉天气？")
        assert result == "今天晴 28 度"  # 前缀已剥
        assert housekeeper.calls[0][0] == "武汉天气？"
        assert housekeeper.calls[0][1].startswith("ask:housekeeper")  # 独立会话
    finally:
        team_bus.clear()


async def test_ask_role_depth_limit() -> None:
    """一层协作深度：被 ask 的岗位内部再 ask → 拒绝。"""
    from chuan import team_bus

    team_bus.register_roles({"a": FakeRole("a", "A岗"), "b": FakeRole("b", "B岗")})
    try:
        token = team_bus._depth.set(1)  # 模拟已在协作层内
        try:
            result = await team_bus._ask_role_async("B岗", "再问一层")
            assert "最大协作深度" in result
        finally:
            team_bus._depth.reset(token)
    finally:
        team_bus.clear()


async def test_ask_role_unknown_and_empty() -> None:
    from chuan import team_bus

    team_bus.register_roles({"housekeeper": FakeRole("housekeeper", "管家")})
    try:
        assert "没有岗位" in await team_bus._ask_role_async("不存在", "问题")
        assert "问题不能为空" in await team_bus._ask_role_async("管家", "  ")
    finally:
        team_bus.clear()


async def test_ask_role_target_error_returns_signal() -> None:
    """目标岗位 dispatch 抛异常 → 返回 [ERROR] 而非向上炸。"""
    from chuan import team_bus

    bad = FakeRole("bad", "坏岗")
    bad.dispatch = None  # type: ignore[assignment]

    async def boom(task, session_id="default"):
        raise RuntimeError("模型超时")

    bad.dispatch = boom  # type: ignore[method-assign]
    team_bus.register_roles({"bad": bad})
    try:
        result = await team_bus._ask_role_async("坏岗", "问题")
        assert result.startswith("[ERROR]") and "模型超时" in result
    finally:
        team_bus.clear()


# ── _summarize ──────────────────────────────────────


def test_summarize_single_result_passthrough() -> None:
    role, _ = _role()
    plan = [SubTask(id="s1", description="唯一任务")]
    results = {"s1": AgentResult(content="直接答案", agent_name="x")}
    assert role._summarize("任务", plan, results) == "直接答案"


def test_summarize_sections_with_failure_mark() -> None:
    role, _ = _role()
    plan = [
        SubTask(id="s1", description="步骤一"),
        SubTask(id="s2", description="步骤二"),
    ]
    results = {
        "s1": AgentResult(content="OK", agent_name="x"),
        "s2": AgentResult(content="炸了", agent_name="x", success=False),
    }
    text = role._summarize("任务", plan, results)
    assert "### 步骤一\nOK" in text
    assert "### 步骤二（失败）\n炸了" in text


# ── dispatch 端到端 ─────────────────────────────────


async def test_dispatch_multi_subtask_end_to_end() -> None:
    role, pool = _role(FakeModel(_PLAN_JSON))
    reply = await role.dispatch("先查武汉天气，然后给穿搭建议")
    assert reply.startswith("[调研员] ")
    # 默认 agent 被调了 2 次（两个子任务），规划模型 1 次
    assert len(pool.default.calls) == 2
    # 汇总包含两个小节，s2 提示含 s1 结果
    assert "### 查武汉天气" in reply
    assert "### 给穿搭建议" in reply
    assert "【前置子任务结果】" in pool.default.calls[1][0]
    assert "查武汉天气" in pool.default.calls[1][0]


async def test_dispatch_plan_fails_falls_back_to_single() -> None:
    role, pool = _role(FakeModel("抱歉我不会拆分"))
    reply = await role.dispatch("先查天气，然后给建议")
    # 规划失败 → 单 agent 兜底，原始任务直接下发
    assert len(pool.default.calls) == 1
    assert pool.default.calls[0][0] == "先查天气，然后给建议"
    assert "结果:先查天气，然后给建议" in reply


async def test_dispatch_planner_says_simple_uses_single() -> None:
    single = '{"subtasks": [{"id": "s1", "description": "直接回答", "agent": "auto", "depends_on": []}]}'
    role, pool = _role(FakeModel(single))
    task = "这一句话虽然超过二十四个字符但是其实是单一简单问题"
    await role.dispatch(task)
    assert len(pool.default.calls) == 1
    assert pool.default.calls[0][0] == task


async def test_dispatch_short_task_skips_planner() -> None:
    model = FakeModel(_PLAN_JSON)
    role, pool = _role(model)
    await role.dispatch("你好")
    assert len(model.prompts) == 0  # 门槛拦截，规划零调用
    assert len(pool.default.calls) == 1


async def test_dispatch_explicit_agent_skips_planner() -> None:
    model = FakeModel(_PLAN_JSON)
    pi = FakeAgent("pi")
    role, _ = _role(model, resident={"pi": pi})
    await role.dispatch("用pi 写个hello world脚本")
    assert len(pi.calls) == 1
    assert pi.calls[0][0] == "写个hello world脚本"  # 前缀被剥掉
    assert len(model.prompts) == 0  # 显式指定不规划


# ── 阶段3：分波并行 ─────────────────────────────────


_PARALLEL_JSON = (
    '{"subtasks": ['
    '{"id": "s1", "description": "查A", "agent": "auto", "depends_on": []},'
    '{"id": "s2", "description": "查B", "agent": "auto", "depends_on": []}'
    "]}"
)


class BarrierAgent:
    """并发协调 agent：所有并行实例到齐才放行（串行会等超时失败）。"""

    def __init__(self, parties: int = 2) -> None:
        self.barrier = asyncio.Barrier(parties)
        self.calls: list[str] = []

    async def run(self, task: str, context: dict | None = None) -> AgentResult:
        self.calls.append(task)
        try:
            await asyncio.wait_for(self.barrier.wait(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
            return AgentResult(
                content=f"串行超时:{task}", agent_name="barrier", success=False
            )
        return AgentResult(content=f"并行:{task}", agent_name="barrier")


async def test_execute_runs_independent_subtasks_concurrently() -> None:
    """两个无依赖子任务必须真并行：串行执行会因 barrier 超时而失败。"""
    role, pool = _role()
    pool.default = BarrierAgent(parties=2)
    plan = [
        SubTask(id="s1", description="查A"),
        SubTask(id="s2", description="查B"),
    ]
    results = await role._execute(plan)
    assert results["s1"].success and results["s2"].success
    assert "并行:" in results["s1"].content


async def test_execute_waves_respect_dependency() -> None:
    """菱形依赖：s1/s2 同波并行，s3 等两者完成后才执行且注入全部结果。"""
    role, pool = _role()
    plan = [
        SubTask(id="s1", description="查A"),
        SubTask(id="s2", description="查B"),
        SubTask(id="s3", description="汇总", depends_on=["s1", "s2"]),
    ]
    results = await role._execute(plan)
    assert len(pool.default.calls) == 3
    # 前两个调用是 s1/s2（顺序无关），第三个是 s3（含依赖注入）
    s3_prompt = pool.default.calls[2][0]
    assert "汇总" in s3_prompt
    assert "【前置子任务结果】" in s3_prompt
    assert "查A" in s3_prompt and "查B" in s3_prompt
    first_two = {c[0] for c in pool.default.calls[:2]}
    assert any("查A" in p for p in first_two) and any("查B" in p for p in first_two)


def test_count_waves() -> None:
    independent = [SubTask(id="s1", description="a"), SubTask(id="s2", description="b")]
    diamond = independent + [SubTask(id="s3", description="c", depends_on=["s1", "s2"])]
    chain = [
        SubTask(id="s1", description="a"),
        SubTask(id="s2", description="b", depends_on=["s1"]),
        SubTask(id="s3", description="c", depends_on=["s2"]),
    ]
    assert Department._count_waves(independent) == 1
    assert Department._count_waves(diamond) == 2
    assert Department._count_waves(chain) == 3


# ── 阶段3：specialist 临时 spawn ────────────────────


async def test_specialist_subtask_spawns_temp_agent() -> None:
    model = FakeModel("x")  # 仅作 _resolve_planner 的模型来源
    role, pool = _role(model)
    plan = [SubTask(id="s1", description="分析数据", specialist="你是数据分析师")]
    results = await role._execute(plan)
    assert len(pool.spawn_calls) == 1
    assert pool.spawn_calls[0]["prompt"] == "你是数据分析师"
    assert pool.spawn_calls[0]["model"] is model
    # spawn 的 agent 被调用，而非默认内置
    assert pool.default.calls == [] or pool.builtin_births == 0
    assert "分析数据" in results["s1"].content


async def test_specialist_agent_cached_by_persona() -> None:
    model = FakeModel("x")
    role, _ = _role(model)
    plan = [SubTask(id="s1", description="任务", specialist="你是数据分析师")]
    await role._execute(plan)
    await role._execute(plan)
    # 同一 specialist 只 spawn 一次（缓存复用）
    assert len([p for p in _pool_of(role).spawn_calls]) == 1


def _pool_of(role: Department) -> FakePool:
    return role.pool  # type: ignore[return-value]


async def test_specialist_falls_back_without_model() -> None:
    role, pool = _role(None)  # 无规划模型 → 无法 spawn
    plan = [SubTask(id="s1", description="任务", specialist="你是分析师")]
    results = await role._execute(plan)
    assert pool.spawn_calls == []  # 没尝试 spawn
    assert len(pool.default.calls) == 1  # 默认内置兜底


# ── 阶段3：进度跟踪 ─────────────────────────────────


async def test_dispatch_emits_progress_events() -> None:
    events: list[dict] = []
    pool = FakePool(model=FakeModel(_PARALLEL_JSON))
    role = Department(
        _Persona(), pool, planner_model=None, on_progress=events.append
    )
    # planner_model=None 时 _resolve_planner 会从 pool.get_model 解析
    await role.dispatch("先查A的资料，再查B的资料")  # 24+ 字符触发规划
    kinds = [e["event"] for e in events]
    assert kinds[0] == "plan"
    assert kinds[-1] == "done"
    assert kinds.count("subtask_start") == 2
    assert kinds.count("subtask_done") == 2
    # 一波内两个 start 都先于第一个 done（真并行）
    first_done = kinds.index("subtask_done")
    assert kinds[:first_done].count("subtask_start") == 2
    # plan 事件带波数与数量
    plan_event = events[0]
    assert plan_event["count"] == 2 and plan_event["waves"] == 1
    # progress 状态表全部落定
    assert role.progress == {"s1": "done", "s2": "done"}


async def test_progress_callback_exception_does_not_break() -> None:
    def boom(event: dict) -> None:
        raise RuntimeError("回调炸了")

    pool = FakePool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool, on_progress=boom)
    reply = await role.dispatch("先查A的资料，再查B的资料")
    assert "结果:" in reply  # 执行不受回调异常影响


# ── AgentPool.spawn_builtin（真 create_react_agent）──


def test_spawn_builtin_creates_and_runs() -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    loader = SimpleNamespace(tools=SimpleNamespace(get_tools=lambda deny=None: []))
    pool = AgentPool(loader)
    model = GenericFakeChatModel(messages=iter([AIMessage(content="专家意见")]))
    agent = pool.spawn_builtin(model, tools=[], system_prompt="你是数据分析师", name="t1")
    assert agent.name == "t1"
    assert agent in pool._temp
    result = asyncio.run(agent.run("分析下数据"))
    assert result.content == "专家意见"
    pool.cleanup_temp()
    assert pool._temp == []


def test_spawn_builtin_default_tools_from_registry() -> None:
    loader = SimpleNamespace(tools=SimpleNamespace(get_tools=lambda deny=None: ["tool_a"]))
    pool = AgentPool(loader)
    with patch("chuan.agent_pool.create_react_agent") as mock_create:
        mock_create.return_value = object()
        pool.spawn_builtin("fake-model", system_prompt="x", name="t2")
        tools_arg = mock_create.call_args[0][1]
        assert tools_arg == ["tool_a"]  # tools=None → ToolRegistry 全集


def test_spawn_builtin_instance_forces_distinct_graph() -> None:
    """N38：spawn_builtin_instance 用 force_rebirth 拿独立图，避开 birth 缓存。"""
    from unittest.mock import Mock

    loader = SimpleNamespace(
        tools=SimpleNamespace(get_tools=lambda deny=None: []),
        _born={},
    )
    graph1, graph2 = object(), object()
    loader.birth = Mock(side_effect=[graph1, graph2])
    pool = AgentPool(loader)
    a1 = pool.spawn_builtin_instance("researcher")
    a2 = pool.spawn_builtin_instance("researcher")
    assert a1 is not a2  # 独立实例
    assert a1._graph is graph1 and a2._graph is graph2  # 独立图
    # 两次都 force_rebirth=True（绕过缓存）
    assert loader.birth.call_count == 2
    for call in loader.birth.call_args_list:
        assert call.kwargs.get("force_rebirth") is True


# ── N37 岗位化 1:N 过渡：岗位多 agent 池 ─────────────


async def test_role_default_agent_enters_pool_after_dispatch() -> None:
    """1:1 基线：简单任务派发后岗位持有默认实例（agent_count==1）。"""
    role, _ = _role()
    await role.dispatch("今天天气如何")
    assert role.agent_count() == 1
    assert role.list_agents() == ["default"]
    assert role._agents["default"] is not None


async def test_spawn_agent_expands_role_to_n() -> None:
    """岗位可显式扩容到 N 个 agent 实例（1:1 → 1:N）。"""
    model = FakeModel("x")
    role, pool = _role(model)
    a1 = role.spawn_agent("writer", system_prompt="你是文案")
    a2 = role.spawn_agent("analyst", system_prompt="你是分析师")
    assert a1 is not a2
    assert role.agent_count() == 2  # 默认实例未创建，只有两个扩容实例
    assert role.list_agents() == ["writer", "analyst"]
    assert pool.spawn_calls[0]["prompt"] == "你是文案"
    assert pool.spawn_calls[0]["name"].startswith("researcher:")
    # 扩容实例可用
    result = await a1.run("写段文案")
    assert result.content == "结果:写段文案"


async def test_spawn_agent_idempotent_by_instance_id() -> None:
    model = FakeModel("x")
    role, pool = _role(model)
    a1 = role.spawn_agent("writer", system_prompt="你是文案")
    a2 = role.spawn_agent("writer", system_prompt="你是别的")
    assert a1 is a2  # 同 id 复用，不重复 spawn
    assert len(pool.spawn_calls) == 1


async def test_spawn_agent_falls_back_without_model() -> None:
    role, pool = _role(None)  # 无模型 → 无法扩容
    agent = role.spawn_agent("writer", system_prompt="你是文案")
    assert agent is pool.default  # 默认实例兜底
    assert pool.spawn_calls == []
    assert role.agent_count() == 1


async def test_default_agent_and_spawned_coexist() -> None:
    """默认实例与扩容实例共存：default + 扩容 = N。"""
    model = FakeModel("x")
    role, _ = _role(model)
    await role.dispatch("今天天气如何")  # 触发默认实例
    role.spawn_agent("writer", system_prompt="你是文案")
    assert role.agent_count() == 2
    assert set(role.list_agents()) == {"default", "writer"}


# ── N37 岗位化 1:N 过渡：会话级状态隔离 ──────────────


async def test_session_progress_isolated_per_session() -> None:
    """同一岗位不同会话的进度存各自 dict，互不串扰。"""
    role, _ = _role(FakeModel(_PARALLEL_JSON))
    await role.dispatch("先查A的资料，再查B的资料", session_id="sessA")
    await role.dispatch("先查A的资料，再查B的资料", session_id="sessB")
    prog_a = role._session_progress["sessA"]
    prog_b = role._session_progress["sessB"]
    assert prog_a is not prog_b  # 独立 dict
    assert prog_a == {"s1": "done", "s2": "done"}
    assert prog_b == {"s1": "done", "s2": "done"}
    # role 级视图指向最近会话（向后兼容）
    assert role.progress is prog_b


class SlowAgent(FakeAgent):
    """带小延时的假 agent，让并发 dispatch 真正交错。"""

    async def run(self, task: str, context: dict | None = None) -> AgentResult:
        await asyncio.sleep(0.005)
        return await super().run(task, context)


async def test_concurrent_sessions_do_not_clobber_progress() -> None:
    """同一岗位并行服务两个会话：进度各写各的，不互相覆盖。"""
    pool = FakePool(model=FakeModel(_PARALLEL_JSON))
    pool.default = SlowAgent("builtin")
    role = Department(_Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON))
    replies = await asyncio.gather(
        role.dispatch("先查A的资料，再查B的资料", session_id="A"),
        role.dispatch("先查A的资料，再查B的资料", session_id="B"),
    )
    assert len(replies) == 2
    assert role._session_progress["A"] == {"s1": "done", "s2": "done"}
    assert role._session_progress["B"] == {"s1": "done", "s2": "done"}
    # 团队状态按会话各写各的 <session_id>.json
    assert set(role._state_writers) == {"A", "B"}


# ── N38 岗位化 1:N 第二台阶：并行子任务独立 worker ──


class _N38Pool(FakePool):
    """带 spawn_builtin_instance 能力的假池（同 persona 独立图实例）。"""

    def __init__(self, model: Any = None, resident: dict | None = None) -> None:
        super().__init__(resident=resident, model=model)
        self.instance_spawns = 0
        self.instance_calls: list[dict] = []

    def spawn_builtin_instance(
        self,
        persona_name: str,
        checkpointer: Any = None,
        *,
        model: Any = None,
        tools: list | None = None,
        system_prompt: str | None = None,
    ) -> FakeAgent:
        self.instance_spawns += 1
        self.instance_calls.append(
            {"name": persona_name, "checkpointer": checkpointer,
             "model": model, "tools": tools, "system_prompt": system_prompt}
        )
        return FakeAgent(name=f"worker:{persona_name}")


async def test_parallel_auto_subtasks_get_distinct_workers() -> None:
    """N38：一波 ≥2 个并行 auto 子任务各分独立 worker 实例（1:N 默认启用）。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON))
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    assert role._agents.get("worker0") is not None
    assert role._agents.get("worker1") is not None
    assert role._agents["worker0"] is not role._agents["worker1"]  # 独立实例
    assert role.agent_count() == 2  # 两个 worker，默认实例未创建
    assert pool.default.calls == []  # 并行子任务没挤在默认实例上
    assert pool.instance_spawns == 2  # worker0/worker1 各 spawn 一次


async def test_parallel_worker_cap_limits_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N38：CHUAN_PARALLEL_WORKERS 封顶并行 worker 数（超出复用）。"""
    monkeypatch.setenv("CHUAN_PARALLEL_WORKERS", "1")
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON))
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    assert role.agent_count() == 1  # 只建 worker0，s1/s2 复用
    assert list(role._agents) == ["worker0"]


async def test_sequential_subtasks_stay_on_default() -> None:
    """N38：串行（有依赖）子任务仍走默认实例，不建 worker。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool)
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="汇总", depends_on=["s1"])])
    assert role.agent_count() == 1  # 只有默认实例
    assert role.list_agents() == ["default"]
    assert len(pool.default.calls) == 2  # 两个子任务都在默认实例


async def test_resolve_sub_agent_uses_role_instance() -> None:
    """N38：子任务指定岗位实例 id → 直接用该实例；未知名回退默认。"""
    model = FakeModel("x")
    role, _ = _role(model)
    writer = role.spawn_agent("writer", system_prompt="你是文案")
    assert role._resolve_sub_agent("writer") is writer
    assert role._resolve_sub_agent("不存在的") is role._ensure_default_agent()


# ── N39 按实例配置：工具 / 模型 / 记忆 ───────────────


async def test_spawn_agent_applies_config_tools_model_memory() -> None:
    """N39：spawn_agent 用 RoleAgentConfig 指定工具/模型/系统提示词/会话存档。"""
    model = FakeModel("x")
    checkpointer = object()
    tools = ["tool_a", "tool_b"]
    role, pool = _role(model)
    cfg = RoleAgentConfig(
        tools=tools, model=model,
        system_prompt="你是数据分析师", checkpointer=checkpointer,
    )
    agent = role.spawn_agent("analyst", config=cfg)
    assert role._agents["analyst"] is agent
    call = pool.spawn_calls[-1]
    assert call["tools"] == tools
    assert call["model"] is model
    assert call["prompt"] == "你是数据分析师"
    assert call["checkpointer"] is checkpointer
    # 实例配置被记录（检视审计）
    recorded = role._agent_configs["analyst"]
    assert recorded.tools == tools and recorded.model is model
    assert recorded.system_prompt == "你是数据分析师"
    assert recorded.checkpointer is checkpointer


async def test_spawn_agent_legacy_params_override_config() -> None:
    """N39：旧关键字参数可覆盖 config 的默认值。"""
    model = FakeModel("x")
    role, pool = _role(model)
    cfg = RoleAgentConfig(tools=["t1"], system_prompt="config提示", model=model)
    role.spawn_agent("w", config=cfg, tools=["t2"], system_prompt="显式提示")
    call = pool.spawn_calls[-1]
    assert call["tools"] == ["t2"]  # 显式参数优先
    assert call["prompt"] == "显式提示"


async def test_worker_respects_role_config() -> None:
    """N39：岗位 worker 配置（_worker_config）贯穿并行 worker 创建。"""
    checkpointer = object()
    model = FakeModel("x")
    cfg = RoleAgentConfig(
        tools=["tool_w"], model=model,
        system_prompt="worker专用", checkpointer=checkpointer,
    )
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON))
    role._worker_config = cfg
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    assert role._agents.get("worker0") is not None
    assert role._agent_configs["worker0"] is cfg  # 配置被记录
    call = pool.instance_calls[0]
    assert call["tools"] == ["tool_w"]
    assert call["model"] is model
    assert call["system_prompt"] == "worker专用"
    assert call["checkpointer"] is checkpointer


# ── N41 动态实例池：用量跟踪 + 自动扩缩容 ─────────────


async def test_worker_execution_updates_usage_stats() -> None:
    """N41：worker 实例实际执行后 last_used_at/uses 更新（扩缩容依据）。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON))
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    assert role._instance_stats["worker0"].uses == 1
    assert role._instance_stats["worker1"].uses == 1
    assert role._instance_stats["worker0"].created_at > 0


async def test_single_task_touches_default_instance() -> None:
    """N41：单任务路径也更新默认实例的用量统计。"""
    role, _ = _role()
    await role.dispatch("今天天气如何")
    assert role._instance_stats["default"].uses == 1


def test_reclaim_idle_removes_old_keeps_min() -> None:
    """N41：回收最久未用且超 TTL 的实例，保留 keep_min 个。"""
    model = FakeModel("x")
    role, _ = _role(model)
    role.spawn_agent("a", system_prompt="x")
    role.spawn_agent("b", system_prompt="x")
    role.spawn_agent("c", system_prompt="x")
    role._instance_stats["a"].last_used_at = time.monotonic() - 1000
    role._instance_stats["b"].last_used_at = time.monotonic()
    role._instance_stats["c"].last_used_at = time.monotonic()
    reclaimed = role.reclaim_idle(idle_ttl=100, keep_min=1)
    assert reclaimed == 1
    assert "a" not in role._agents  # 最久未用的被回收
    assert set(role._agents) == {"b", "c"}


def test_reclaim_idle_keeps_minimum_even_if_all_idle() -> None:
    """N41：即使全部空闲，也保留 keep_min 个非默认实例（缩容下限）。"""
    model = FakeModel("x")
    role, _ = _role(model)
    role.spawn_agent("a", system_prompt="x")
    role.spawn_agent("b", system_prompt="x")
    role._instance_stats["a"].last_used_at = time.monotonic() - 1000
    role._instance_stats["b"].last_used_at = time.monotonic() - 1000
    reclaimed = role.reclaim_idle(idle_ttl=10, keep_min=1)
    assert reclaimed == 1
    assert len(role._agents) == 1  # 只剩 1 个非默认


def test_reclaim_idle_never_recycles_default() -> None:
    """N41：默认实例是岗位身份，永不回收。"""
    model = FakeModel("x")
    role, _ = _role(model)
    role.spawn_agent("w", system_prompt="x")
    role._ensure_default_agent()
    role._instance_stats["default"].last_used_at = time.monotonic() - 1000
    role._instance_stats["w"].last_used_at = time.monotonic() - 1000
    reclaimed = role.reclaim_idle(idle_ttl=1, keep_min=0)
    assert reclaimed == 1  # 只回收 w
    assert "default" in role._agents


def test_reclaim_idle_respects_pool_config_defaults() -> None:
    """N41：未显式传参时取池配置的 TTL/下限。"""
    model = FakeModel("x")
    role, _ = _role(model)
    role._pool_config = RolePoolConfig(min_instances=0, max_instances=3, idle_ttl=100)
    role.spawn_agent("a", system_prompt="x")
    role.spawn_agent("b", system_prompt="x")
    role._instance_stats["a"].last_used_at = time.monotonic() - 1000
    role._instance_stats["b"].last_used_at = time.monotonic()
    assert role.reclaim_idle() == 1  # 只回收超 TTL 的 a
    assert set(role._agents) == {"b"}


def test_pool_stats_reports_capacity_and_usage() -> None:
    """N41：pool_stats 暴露容量/空闲/用量（供 TUI/心跳观测）。"""
    model = FakeModel("x")
    role, _ = _role(model)
    role._pool_config = RolePoolConfig(min_instances=1, max_instances=3, idle_ttl=100)
    role.spawn_agent("a", system_prompt="x")
    role.spawn_agent("b", system_prompt="x")
    role._instance_stats["a"].last_used_at = time.monotonic() - 1000  # 空闲超 TTL
    role._instance_stats["b"].last_used_at = time.monotonic()  # 在用
    stats = role.pool_stats()
    assert stats["size"] == 2
    assert stats["min"] == 1 and stats["max"] == 3
    assert stats["idle"] == 1  # 只有 a 判空闲
    assert stats["uses"] == {"a": 0, "b": 0}


async def test_parallel_cap_uses_pool_max() -> None:
    """N41：开启动态池时扩容上限取 pool.max_instances（而非 CHUAN_PARALLEL_WORKERS）。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(
        _Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON),
        pool_config=RolePoolConfig(min_instances=1, max_instances=1, idle_ttl=300.0),
    )
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    assert list(role._agents) == ["worker0"]  # 上限 1 → 只建 worker0，s2 复用
    assert pool.instance_spawns == 1


async def test_dispatch_auto_reclaims_idle_workers() -> None:
    """N41 集成：dispatch 开工前自动回收空闲 worker（保留 min 下限）。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(
        _Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON),
        pool_config=RolePoolConfig(min_instances=1, max_instances=3, idle_ttl=0.0),
    )
    # 第一波并行 → 建 worker0/worker1
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    assert role.agent_count() == 2
    # 下一波 dispatch 开工 → 空闲(ttl=0) worker 回收，保留 min=1 个非默认
    await role.dispatch("今天天气如何")  # simple 单任务，仍触发开工回收
    assert set(role._agents) == {"default", "worker1"}  # 最久未用的 worker0 被回收


async def test_dispatch_does_not_reclaim_without_pool_config() -> None:
    """N41：未开启动态池（pool_config=None）→ 开工不回收，兼容旧行为。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = Department(_Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON))
    await role._execute([SubTask(id="s1", description="查A"),
                         SubTask(id="s2", description="查B")])
    await role.dispatch("今天天气如何")
    assert role.agent_count() == 3  # worker0/worker1/default 都保留


# ── 悬空 tool_calls 历史修复（dispatch 前置修）──────────────────────

class _RepairableAgent(FakeAgent):
    """带 _repair_history 的 FakeAgent 替身——记录被调用的 thread_id。"""

    def __init__(self, name: str = "repair_agent") -> None:
        super().__init__(name=name)
        self.repairs: list[str] = []

    async def _repair_history(self, config: dict[str, Any]) -> None:
        self.repairs.append(config["configurable"]["thread_id"])


@pytest.mark.asyncio
async def test_ensure_history_ok_calls_agent_repair_history() -> None:
    """Department._ensure_agent_history_ok 调 BuiltinAgent 型 agent 的 _repair_history
    并透传 thread_id；无此方法的 agent 直接跳过不报错。"""
    rep = _RepairableAgent()
    await Department._ensure_agent_history_ok(rep, "t_abc")
    assert rep.repairs == ["t_abc"]

    plain = FakeAgent()
    await Department._ensure_agent_history_ok(plain, "t_xyz")  # 不应抛错


@pytest.mark.asyncio
async def test_ensure_history_ok_repair_failure_is_silent() -> None:
    """agent._repair_history 抛错 → 静默吞，不影响上层。"""

    class _BoomAgent(FakeAgent):
        async def _repair_history(self, config: dict[str, Any]) -> None:
            raise RuntimeError("checkpointer gone")

    await Department._ensure_agent_history_ok(_BoomAgent(), "t_boom")


@pytest.mark.asyncio
async def test_dispatch_single_agent_runs_history_repair() -> None:
    """单 agent 路径（阶段1 兜底）：dispatch 先 _repair_history(session_id) 再 run。"""
    role, pool = _role()
    # 覆盖 _resolve_tier_instance 返回可观测的 repairable agent
    agent = _RepairableAgent()
    pool.default = agent  # type: ignore[assignment]
    await role.dispatch("你好", session_id="sess_default_repair")
    assert agent.repairs == ["sess_default_repair"]
    assert agent.calls  # run 真正执行了
    assert agent.calls[0][1] and agent.calls[0][1].get("thread_id") == "sess_default_repair"


@pytest.mark.asyncio
async def test_subtask_attempt_runs_history_repair_before_run() -> None:
    """多子任务路径：_run_subtask 每次 attempt 先修对应 thread，再 agent.run。

    直接构造子任务并执行（不走规划模型 → 规划输出/解析不确定性），
    验证每次 attempt 前 _ensure_agent_history_ok 的 thread_id 正确。"""
    role, pool = _role()
    agent = _RepairableAgent()
    pool.default = agent  # type: ignore[assignment]
    await role._run_subtask(
        SubTask(id="s42", description="查天气", depends_on=[]),
        results={}, session_id="sess_x", wave=1, task="总任务",
    )
    # subtask 的 thread 格式为 session:plan:st.id:a<attempt>，首次 attempt=0
    assert any(":s42:a0" in t for t in agent.repairs)
    # run 的 context 里 thread_id 也一致
    threads = {c[1].get("thread_id") for c in agent.calls if c[1]}
    assert any(":s42:a0" in t for t in threads)
