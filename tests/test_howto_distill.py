"""N27 任务收尾自动提炼 → staging 待人工确认：门槛 / 提炼 / 确认 / 角色挂接。"""

from __future__ import annotations

from pathlib import Path

import chuan.howto_distill as hd
from chuan.agents.base import AgentResult
from chuan.howto import HowToStore
from chuan.howto_distill import HowToDistiller
from chuan.memory import Memory
from chuan.role import Department


def _memory(tmp_path: Path) -> Memory:
    return Memory(vault_path=tmp_path / "vault")


def _task() -> str:
    return "帮我部署周报，周五要发"


def _content() -> str:
    return "1. 汇总本周部署变更\n2. 列出影响范围\n3. 生成周报并发送给团队确认\n4. 归档到 wiki"


class _FakeBrain:
    """固定回复的假脑：验证 LLM 润色路径与退化回退。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, prompt: str, system: str = "", temperature: float = 0.0) -> str:
        return self.text


# --------------------------------------------------------------------- #
# 门槛
# --------------------------------------------------------------------- #
def test_distill_success_writes_staging_not_howto(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    cand = HowToDistiller(memory).maybe_distill(
        _task(), _content(), source="role:operator"
    )
    assert cand is not None
    assert cand["name"] == "部署周报"
    assert cand["trigger"] == "部署周报，周五要发"  # 剥「帮我」前缀
    assert "汇总本周部署变更" in cand["process"]
    # 未入库：howto 目录无原子，仅 staging 队列有候选
    store = HowToStore(memory)
    assert store.get("部署周报") is None
    assert len(store.staging_list()) == 1
    assert store.staging_get("部署周报") is not None


def test_distill_skips_failed_task(tmp_path: Path) -> None:
    distiller = HowToDistiller(_memory(tmp_path))
    assert distiller.maybe_distill(_task(), _content(), success=False) is None


def test_distill_skips_short_task(tmp_path: Path) -> None:
    distiller = HowToDistiller(_memory(tmp_path))
    assert distiller.maybe_distill("查天气", _content(), success=True) is None


def test_distill_skips_no_substance(tmp_path: Path) -> None:
    distiller = HowToDistiller(_memory(tmp_path))
    assert distiller.maybe_distill(_task(), "好的，已搞定", success=True) is None


def test_distill_skips_when_already_covered(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    HowToStore(memory).save(
        "部署周报", "每周五 汇总部署 周报", "1. 汇总变更\n2. 生成周报", source="s"
    )
    assert HowToDistiller(memory).maybe_distill(
        "帮我部署周报，周五要发", _content(), success=True
    ) is None


def test_distill_skips_duplicate_pending(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    distiller = HowToDistiller(memory)
    assert distiller.maybe_distill(_task(), _content(), source="a") is not None
    assert distiller.maybe_distill(_task(), _content(), source="b") is None
    assert len(HowToStore(memory).staging_list()) == 1


def test_distill_staging_cap(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(hd, "_MAX_STAGING", 1)
    memory = _memory(tmp_path)
    distiller = HowToDistiller(memory)
    assert distiller.maybe_distill(_task(), _content(), source="a") is not None
    # 队列已满 → 新任务不沉淀
    assert distiller.maybe_distill(
        "帮我整理 Obsidian 笔记并归档到正确目录", _content(), source="b"
    ) is None


# --------------------------------------------------------------------- #
# 提炼：确定性回退 + LLM 润色
# --------------------------------------------------------------------- #
def test_refine_deterministic_no_brain(tmp_path: Path) -> None:
    d = HowToDistiller(_memory(tmp_path), brain=None)
    name, trigger, process, tools = d._refine(_task(), _content())
    assert name == "部署周报"
    assert "部署周报" in trigger
    assert "汇总本周部署变更" in process
    assert tools == []


def test_refine_llm_good_json(tmp_path: Path) -> None:
    brain = _FakeBrain(
        '{"name": "周报生成", "trigger": "每周五部署后", '
        '"process": "步骤A\\n步骤B", "tools": ["bash", "pi"]}'
    )
    d = HowToDistiller(_memory(tmp_path), brain=brain)
    name, trigger, process, tools = d._refine(_task(), _content())
    assert name == "周报生成"
    assert trigger == "每周五部署后"
    assert "步骤A" in process
    assert tools == ["bash", "pi"]


def test_refine_llm_degenerate_falls_back(tmp_path: Path) -> None:
    d = HowToDistiller(
        _memory(tmp_path), brain=_FakeBrain("抱歉，我无法解析这个任务。")
    )
    name, trigger, process, tools = d._refine(_task(), _content())
    assert name == "部署周报"  # 回退确定性提取
    assert "汇总本周部署变更" in process
    assert tools == []


# --------------------------------------------------------------------- #
# 人工确认：approve / discard
# --------------------------------------------------------------------- #
def test_approve_writes_into_howto_with_wiki_features(tmp_path: Path) -> None:
    from chuan.wiki import Wiki

    memory = _memory(tmp_path)
    distiller = HowToDistiller(memory)
    cand = distiller.maybe_distill(_task(), _content(), source="role:operator")
    assert cand is not None

    store = HowToStore(memory)
    assert store.get(cand["name"]) is None  # 确认前未入库
    path = store.approve(cand["name"])
    assert path is not None and path.name == "部署周报.md"

    text = path.read_text(encoding="utf-8")
    assert "## 触发场景" in text and "## 怎么做" in text
    assert store.staging_list() == []  # 队列已清

    # wiki 底座生效：index 收录、lint 覆盖、可被检索
    index = memory.notes_path / "howto" / "index.md"
    assert "[[howto/部署周报]]" in index.read_text(encoding="utf-8")
    report = Wiki(memory).lint()
    assert "howto/部署周报.md" not in report["missing_meta"]
    hits = Wiki(memory).search_index("部署周报")
    assert hits and hits[0]["rel_path"] == "howto/部署周报.md"


def test_approve_rename(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    cand = HowToDistiller(memory).maybe_distill(_task(), _content(), source="s")
    assert cand is not None
    path = HowToStore(memory).approve(cand["name"], rename="周报生成做法")
    assert path is not None and path.name == "周报生成做法.md"


def test_discard_removes_candidate(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    cand = HowToDistiller(memory).maybe_distill(_task(), _content(), source="s")
    store = HowToStore(memory)
    assert cand is not None
    assert store.discard(cand["name"]) is True
    assert store.staging_list() == []
    assert store.get(cand["name"]) is None  # 未入库
    assert store.discard(cand["name"]) is False  # 幂等


def test_staging_list_returns_all(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("原子A", "触发A", "过程A", task="任务A")
    store.stage("原子B", "触发B", "过程B", task="任务B")
    names = {c["name"] for c in store.staging_list()}
    assert names == {"原子A", "原子B"}


# --------------------------------------------------------------------- #
# 角色挂接（_wrap_result 收尾自动沉淀）
# --------------------------------------------------------------------- #
def _role(memory: Memory) -> Department:
    from types import SimpleNamespace

    persona = SimpleNamespace(name="worker", display_name="工", description="")
    return Department(persona, agent_pool=None, memory=memory)


def test_role_wrap_result_distills_success(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    role = _role(memory)
    out = role._wrap_result(
        AgentResult(content=_content(), agent_name="builtin", success=True), _task()
    )
    assert out.startswith("[工]")
    pending = HowToStore(memory).staging_list()
    assert len(pending) == 1 and pending[0]["name"] == "部署周报"


def test_role_wrap_result_skips_failure(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    role = _role(memory)
    role._wrap_result(
        AgentResult(content=_content(), agent_name="builtin", success=True), _task()
    )
    role._wrap_result(
        AgentResult(content="出错了", agent_name="builtin", success=False), _task()
    )
    assert len(HowToStore(memory).staging_list()) == 1  # 失败不新增


def test_role_no_memory_unchanged(tmp_path: Path) -> None:
    from types import SimpleNamespace

    persona = SimpleNamespace(name="worker", display_name="工", description="")
    role = Department(persona, agent_pool=None, memory=None)
    out = role._wrap_result(
        AgentResult(content=_content(), agent_name="builtin", success=True), _task()
    )
    assert out.startswith("[工]")  # 无 memory 时自动沉淀被跳过


# --------------------------------------------------------------------- #
# N27 主流程集成：RuntimeSupervisor 钩子（确认/否决 + 追加提示）
# --------------------------------------------------------------------- #
from chuan.runtime_supervisor import RuntimeSupervisor  # noqa: E402


class _SupLike(RuntimeSupervisor):
    """仅暴露 memory 的最小 RuntimeSupervisor 替身（继承全部 N27 钩子）。"""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory


def _sup_obj(memory: Memory) -> RuntimeSupervisor:
    return _SupLike(memory)


def test_resolve_confirm_single_approves(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("部署周报", "每周五 汇总部署 周报", "1. 汇总变更\n2. 生成周报",
                source="role:operator", task="帮我部署周报，周五要发")
    result = RuntimeSupervisor._resolve_pending_howto(_sup_obj(memory), "确认")
    assert result is not None
    assert result["route"] == "howto_confirm"
    assert "已确认沉淀" in result["messages"][-1]["content"]
    assert store.staging_list() == []
    assert store.get("部署周报") is not None  # 已入库


def test_resolve_deny_single_discards(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("部署周报", "每周五 汇总部署", "1. 汇总\n2. 生成", task="A")
    result = RuntimeSupervisor._resolve_pending_howto(_sup_obj(memory), "丢弃")
    assert result is not None
    assert "已丢弃" in result["messages"][-1]["content"]
    assert store.staging_list() == []
    assert store.get("部署周报") is None  # 未入库


def test_resolve_confirm_by_name(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("部署周报", "每周五 汇总部署", "1. 汇总", task="A")
    store.stage("笔记归档", "Obsidian 笔记 归档", "1. 归类", task="B")
    result = RuntimeSupervisor._resolve_pending_howto(_sup_obj(memory), "确认 部署周报")
    assert result is not None
    assert "已确认沉淀知识原子「部署周报」" in result["messages"][-1]["content"]
    assert {c["name"] for c in store.staging_list()} == {"笔记归档"}
    assert store.get("部署周报") is not None


def test_resolve_multiple_bare_word_lists(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("部署周报", "每周五 汇总部署", "1. 汇总", task="A")
    store.stage("笔记归档", "Obsidian 笔记 归档", "1. 归类", task="B")
    result = RuntimeSupervisor._resolve_pending_howto(_sup_obj(memory), "确认")
    assert result is not None
    body = result["messages"][-1]["content"]
    assert "请指定一条" in body and "部署周报" in body and "笔记归档" in body
    assert len(store.staging_list()) == 2  # 未擅自处理


def test_resolve_no_pending_returns_none(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    assert RuntimeSupervisor._resolve_pending_howto(_sup_obj(memory), "确认") is None


def test_resolve_non_intent_returns_none(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    HowToStore(memory).stage("部署周报", "每周五 汇总部署", "1. 汇总", task="A")
    # 正常任务消息不被当成确认
    assert RuntimeSupervisor._resolve_pending_howto(
        _sup_obj(memory), "帮我部署周报，周五要发") is None


def test_resolve_confirm_unknown_name(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("部署周报", "每周五 汇总部署", "1. 汇总", task="A")
    result = RuntimeSupervisor._resolve_pending_howto(_sup_obj(memory), "确认 不存在的")
    assert result is not None
    assert "没有名为" in result["messages"][-1]["content"]
    assert len(store.staging_list()) == 1  # 未误动


def test_resolve_confirm_by_name_case_insensitive(tmp_path: Path) -> None:
    """按名确认大小写不敏感：输入大小写不同也能命中，回复保留存储时的原文大小写。"""
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("整理 Obsidian 笔记并归档", "Obsidian 笔记 归档", "1. 归类", task="A")
    # 输入用与存储不同的大小写（OBSIDIAN 全大写）也应命中
    result = RuntimeSupervisor._resolve_pending_howto(
        _sup_obj(memory), "确认 整理 OBSIDIAN 笔记并归档")
    assert result is not None
    # 回复保留存储时的原文大小写，而非被 lower 吞掉
    assert "已确认沉淀知识原子「整理 Obsidian 笔记并归档」" in result["messages"][-1]["content"]
    assert store.get("整理 Obsidian 笔记并归档") is not None
    assert store.staging_list() == []


def test_resolve_discard_by_name_case_insensitive(tmp_path: Path) -> None:
    """按名否决大小写不敏感：输入大小写不同也能命中并丢弃。"""
    memory = _memory(tmp_path)
    store = HowToStore(memory)
    store.stage("整理 Obsidian 笔记并归档", "Obsidian 笔记 归档", "1. 归类", task="A")
    result = RuntimeSupervisor._resolve_pending_howto(
        _sup_obj(memory), "丢弃 整理 obsidian 笔记并归档")
    assert result is not None
    assert "已丢弃知识原子候选「整理 Obsidian 笔记并归档」" in result["messages"][-1]["content"]
    assert store.staging_list() == []
    assert store.get("整理 Obsidian 笔记并归档") is None  # 未入库


def test_append_howto_prompt_adds_hint_when_newly_staged(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    result = {"messages": [{"role": "assistant", "content": "已完成"}]}
    RuntimeSupervisor._append_howto_prompt(_sup_obj(memory), result, before=set())
    assert "[待确认]" not in result["messages"][-1]["content"]  # 无新增不追加

    HowToStore(memory).stage("部署周报", "每周五 汇总部署", "1. 汇总", task="A")
    result2 = {"messages": [{"role": "assistant", "content": "已完成"}]}
    RuntimeSupervisor._append_howto_prompt(_sup_obj(memory), result2, before=set())
    body = result2["messages"][-1]["content"]
    assert "[待确认]" in body and "部署周报" in body


def test_append_howto_prompt_no_op_when_no_new(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    HowToStore(memory).stage("部署周报", "每周五 汇总部署", "1. 汇总", task="A")
    result = {"messages": [{"role": "assistant", "content": "已完成"}]}
    RuntimeSupervisor._append_howto_prompt(
        _sup_obj(memory), result, before={"部署周报"})
    assert result["messages"][-1]["content"] == "已完成"
