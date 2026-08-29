"""N33 ACI 预判注入：并行预取 memory+wiki 上下文、注入块渲染、岗位透传、面板接口。"""

from __future__ import annotations

from pathlib import Path

from chuan.aci import AciPrefetcher
from chuan.memory import Memory
from chuan.runtime_supervisor import RuntimeSupervisor
from chuan.wiki import Wiki


def _memory(tmp_path: Path) -> Memory:
    return Memory(vault_path=tmp_path / "vault")


def _seed(tmp_path: Path) -> Memory:
    """预置一条长期记忆 + 一个 wiki 实体页。"""
    memory = _memory(tmp_path)
    memory.remember(
        "deploy_notes", "部署周报要点：每周五汇总变更、检查回滚点、发版前备份数据库。",
        importance=4, tags=["deploy"], type="process",
    )
    Wiki(memory).write(
        "projects", "登录重构", "重构登录模块：拆 OAuth + 双因素认证两步，先后端后前端。",
        tags=["auth"],
    )
    return memory


# --------------------------------------------------------------------- #
# 预取核心：并行召回 + 命中过滤
# --------------------------------------------------------------------- #
def test_prefetch_memory_and_wiki_hits(tmp_path: Path) -> None:
    memory = _seed(tmp_path)
    aci = AciPrefetcher(memory)
    bundle = aci.prefetch("帮我做部署周报")
    assert bundle["memory"], "应命中部署记忆"
    assert any("deploy" in h["path"] for h in bundle["memory"])
    # 记忆命中带 snippet + score
    h = bundle["memory"][0]
    assert h["snippet"] and h["score"] > 0


def test_prefetch_wiki_via_token_recall(tmp_path: Path) -> None:
    memory = _seed(tmp_path)
    aci = AciPrefetcher(memory)
    bundle = aci.prefetch("登录模块怎么重构")
    # wiki 实体页（notes/projects/登录重构.md）按 token 级匹配命中
    assert any("登录" in h.get("rel_path", "") for h in bundle["wiki"])


def test_prefetch_no_hits_returns_empty(tmp_path: Path) -> None:
    memory = _memory(tmp_path)  # 空库
    aci = AciPrefetcher(memory)
    bundle = aci.prefetch("随便说点不存在的")
    assert bundle["memory"] == [] and bundle["wiki"] == []


def test_prefetch_empty_message_is_noop(tmp_path: Path) -> None:
    aci = AciPrefetcher(_seed(tmp_path))
    for msg in ("", "   "):
        bundle = aci.prefetch(msg)
        assert bundle["memory"] == [] and bundle["wiki"] == []


def test_prefetch_none_memory_is_safe() -> None:
    aci = AciPrefetcher(None)
    bundle = aci.prefetch("部署周报")
    assert bundle["memory"] == [] and bundle["wiki"] == []


def test_prefetch_parallel_sources_isolated(tmp_path: Path) -> None:
    """一个源异常不影响另一个源（旁路隔离）。"""
    memory = _seed(tmp_path)

    def boom(message: str) -> list:
        raise RuntimeError("wiki 挂了")

    aci = AciPrefetcher(memory)
    aci._recall_wiki = boom  # type: ignore[method-assign]
    bundle = aci.prefetch("部署周报")
    assert bundle["memory"], "memory 源不受 wiki 异常影响"
    assert bundle["wiki"] == []


def test_prefetch_low_score_filtered(tmp_path: Path) -> None:
    memory = _seed(tmp_path)
    # 阈值很高 → 全部被过滤
    aci = AciPrefetcher(memory, min_score=10_000.0)
    bundle = aci.prefetch("部署周报")
    assert bundle["memory"] == []


# --------------------------------------------------------------------- #
# 渲染与统计
# --------------------------------------------------------------------- #
def test_render_returns_block_when_hits(tmp_path: Path) -> None:
    memory = _seed(tmp_path)
    Wiki(memory).write(
        "topics", "部署周报", "部署周报撰写流程：汇总变更清单、标注风险、附回滚计划。",
        tags=["deploy"],
    )
    aci = AciPrefetcher(memory)
    bundle = aci.prefetch("部署周报")
    block = AciPrefetcher.render(bundle)
    assert "【预判上下文】" in block
    assert "[记忆]" in block
    assert "[知识]" in block


def test_render_empty_when_no_hits() -> None:
    block = AciPrefetcher.render({"memory": [], "wiki": []})
    assert block == ""


def test_stats_tracks_last_prefetch(tmp_path: Path) -> None:
    aci = AciPrefetcher(_seed(tmp_path))
    assert aci.stats()["total"] == 0  # 尚未预取
    aci.prefetch("部署周报")
    st = aci.stats()
    assert st["total"] == st["memory"] + st["wiki"]
    assert st["total"] > 0 and st["injected"] is True


# --------------------------------------------------------------------- #
# 岗位透传：dispatch 接受 aci_context 并前置到任务
# --------------------------------------------------------------------- #
def test_role_dispatch_prepends_aci_context(tmp_path: Path) -> None:
    from chuan.aci import AciPrefetcher as AP

    memory = _seed(tmp_path)
    aci = AP(memory)
    bundle = aci.prefetch("部署周报")
    block = AP.render(bundle)
    assert block

    seen: dict[str, str] = {}

    class FakeAgent:
        name = "fake"

        async def run(self, task: str, context=None):
            seen["task"] = task
            return SimpleResult(task)

    class FakePool:
        def get(self, name):
            return None

        def list_resident(self):
            return []

        def get_model(self, name):
            return None

        def get_builtin_agent(self, name, checkpointer=None):
            return FakeAgent()

    from chuan.role import Department

    role = Department(
        SimplePersona("housekeeper"), FakePool(), memory=memory
    )
    import asyncio

    reply = asyncio.run(role.dispatch("帮我做部署周报", aci_context=block))
    assert "【预判上下文】" in seen["task"]
    assert "帮我做部署周报" in seen["task"]
    assert reply  # 人设包装后的回复


def test_role_dispatch_no_aci_passthrough(tmp_path: Path) -> None:
    """不传 aci_context 时任务原样（不注入）。"""
    from chuan.role import Department

    seen: dict[str, str] = {}

    class FakeAgent:
        name = "fake"

        async def run(self, task: str, context=None):
            seen["task"] = task
            return SimpleResult(task)

    class FakePool:
        def get(self, name):
            return None

        def list_resident(self):
            return []

        def get_model(self, name):
            return None

        def get_builtin_agent(self, name, checkpointer=None):
            return FakeAgent()

    import asyncio

    role = Department(SimplePersona("housekeeper"), FakePool(), memory=None)
    asyncio.run(role.dispatch("简单任务"))
    assert seen["task"] == "简单任务"


# --------------------------------------------------------------------- #
# 幕僚长接口
# --------------------------------------------------------------------- #
class _SupLike(RuntimeSupervisor):
    """最小幕僚长替身：只含 aci 字段，验证面板接口不依赖完整唤醒。"""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.aci = AciPrefetcher(memory)


def test_supervisor_aci_status(tmp_path: Path) -> None:
    memory = _seed(tmp_path)
    sup = _SupLike(memory)
    st = sup.aci_status()
    assert st["total"] == 0  # 初始空态，不抛异常


def test_supervisor_aci_prefetch_block(tmp_path: Path) -> None:
    memory = _seed(tmp_path)
    sup = _SupLike(memory)
    block = sup._aci_prefetch_block("部署周报")
    assert "【预判上下文】" in block
    st = sup.aci_status()
    assert st["total"] > 0 and st["injected"] is True


# --------------------------------------------------------------------- #
# 工具类
# --------------------------------------------------------------------- #
class SimplePersona:
    def __init__(self, name: str) -> None:
        self.name = name
        self.display_name = name
        self.description = "测试角色"


class SimpleResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.success = True
        self.agent_name = "fake"
