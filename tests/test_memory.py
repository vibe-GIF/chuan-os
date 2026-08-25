import os
from pathlib import Path

import pytest

from chuan.memory import Memory
from chuan.memory_tools import build_memory_tools


def test_remember_and_recall_markdown(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    path = memory.remember("contract", "甲方应在签署前承担全部合同风险。")

    assert path == tmp_path / "vault" / "notes" / "contract.md"
    hits = memory.recall("合同风险")
    assert [hit.relative_path for hit in hits] == ["notes/contract.md"]
    assert "甲方" in hits[0].content


def test_blackboard_external_namespace_isolated(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    memory.write_blackboard("plan", "核心团队计划")
    external_path = memory.write_blackboard(
        "plan", "外来团队计划", external_agent="example"
    )

    assert external_path == (
        tmp_path / "vault" / "shared" / "external" / "example" / "plan.md"
    )
    assert memory.read_blackboard("plan") == "核心团队计划\n"
    assert memory.read_blackboard("plan", external_agent="example") == "外来团队计划\n"
    assert memory.list_blackboard(external_agent="example") == ["plan"]


@pytest.mark.parametrize("name", ["../escape", "two words", "nested/name"])
def test_memory_rejects_unsafe_document_names(tmp_path: Path, name: str) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    with pytest.raises(ValueError):
        memory.remember(name, "nope")


def test_recall_uses_fts_index(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    memory.remember("contract", "甲方应在签署前承担全部合同风险。")
    memory.remember("weather", "今天的武汉天气晴朗温暖。")

    hits = memory.recall("合同风险")
    assert [h.relative_path for h in hits] == ["notes/contract.md"]

    # 中文分词拆成单字后也走 FTS 命中
    assert memory.recall("武汉天气")[0].relative_path == "notes/weather.md"


def test_reindex_picks_up_external_file(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    # 绕过 remember() 直接写盘，模拟外部手写文档
    external = tmp_path / "vault" / "notes" / "handwritten.md"
    external.parent.mkdir(parents=True, exist_ok=True)
    external.write_text("这份手写笔记记录了密码重置流程。", encoding="utf-8")

    # 未 reindex 前，FTS 为空 → 会触发全盘回退，仍能召回
    assert memory.recall("密码")[0].relative_path == "notes/handwritten.md"

    # reindex 后索引纳入，召回路径保持正确
    assert memory.reindex(namespace="notes") == 1
    assert memory.recall("重置流程")[0].relative_path == "notes/handwritten.md"


def test_reindex_incremental_by_mtime(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    note = tmp_path / "vault" / "notes" / "manual.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("第一版：重置流程已记录。", encoding="utf-8")
    os.utime(note, (1_700_000_000, 1_700_000_000))

    # 首次 reindex：新文件纳入
    assert memory.reindex(namespace="notes") == 1
    assert memory.recall("重置流程")[0].relative_path == "notes/manual.md"

    # mtime 未变 → 全部跳过，0 变更
    assert memory.reindex(namespace="notes") == 0

    # 内容修改且 mtime 前进 → 仅重建该条
    note.write_text("第二版：新增找回密码的步骤。", encoding="utf-8")
    os.utime(note, (1_700_000_100, 1_700_000_100))
    assert memory.reindex(namespace="notes") == 1
    assert memory.recall("找回密码")[0].relative_path == "notes/manual.md"

    # 删除文件 → 清除索引残留（meta 与 FTS 均无脏行）
    note.unlink()
    assert memory.reindex(namespace="notes") == 1
    vault_key = memory._vault_key()
    assert memory._ensure_fts().execute(
        "SELECT COUNT(*) FROM memory_meta WHERE vault=?", (vault_key,)
    ).fetchone()[0] == 0
    assert memory.recall("重置流程") == []


def test_remember_writes_frontmatter_meta(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    path = memory.remember(
        "deploy_notes",
        "部署前必须先跑测试。",
        importance=4,
        tags=["运维", "部署"],
        source="session:abc",
    )
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    assert "importance: 4" in raw
    assert "运维" in raw

    hit = memory.recall("部署")[0]
    assert hit.importance == 4
    assert set(hit.tags) == {"运维", "部署"}
    assert "---" not in hit.content  # 召回正文不带 frontmatter
    assert "部署前必须先跑测试" in hit.content


def test_recall_min_importance_gates(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    memory.remember("draft", "草稿：服务器扩容初步想法。", importance=1)
    memory.remember("plan", "正式方案：服务器扩容到 4 台。", importance=4)

    # 默认不过滤 → 两条都召回
    assert len(memory.recall("服务器扩容")) == 2
    # 门控 3 → 只留 importance>=3 的正式方案
    gated = memory.recall("服务器扩容", min_importance=3)
    assert [hit.relative_path for hit in gated] == ["notes/plan.md"]


def test_legacy_note_without_frontmatter_still_recalls(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    note = tmp_path / "vault" / "notes" / "old.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("旧笔记：没有 frontmatter。", encoding="utf-8")

    hit = memory.recall("旧笔记")[0]
    assert hit.importance == 0  # 旧文档按 importance=0 处理
    # 默认门控 0 不拦截旧文档
    assert hit.relative_path == "notes/old.md"
    # 提高门控后旧文档被过滤
    assert memory.recall("旧笔记", min_importance=1) == []


def test_remember_overwrite_preserves_created(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    first = memory.remember("note", "v1", source="a").read_text(encoding="utf-8")
    created = next(line for line in first.splitlines() if line.startswith("created:"))
    second = memory.remember("note", "v2 内容", source="b").read_text(encoding="utf-8")
    assert created in second  # 覆盖写保留原 created
    assert "updated" in second


def test_memory_tools_remember_and_recall(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    tools = {t.name: t for t in build_memory_tools(memory)}

    assert {"remember_memory", "recall_memory"} <= set(tools)

    result = tools["remember_memory"].func("deploy_notes", "部署前必须先跑测试。")
    assert result.startswith("已记录到")

    recalled = tools["recall_memory"].func("部署流程")
    assert "deploy_notes" in recalled
    assert "部署前必须先跑测试" in recalled

    # 无命中 → 明确提示
    assert "没有找到" in tools["recall_memory"].func("毫不相关的关键词xyz")


def test_memory_tools_are_multi_input(tmp_path: Path) -> None:
    """记忆工具必须是多输入结构化工具（回归：裸 Tool 单输入会炸多参调用）。

    模型按参数调用 remember_memory(name, content, importance, tags, ...) 时，
    单输入工具会报「Too many arguments to single-input tool」。用
    StructuredTool.from_function 后 args 暴露全字段，且 ainvoke 传 dict 正常。
    """
    import asyncio

    memory = Memory(vault_path=tmp_path / "vault")
    tools = {t.name: t for t in build_memory_tools(memory)}

    # 多输入：args 是各参数字段，而非单 tool_input
    assert list(tools["remember_memory"].args) == [
        "name", "content", "importance", "tags", "source", "type"
    ]
    assert list(tools["recall_memory"].args) == [
        "query", "limit", "min_importance", "type"
    ]
    # ainvoke 传 dict（agent 工具调用路径）正常执行
    out = asyncio.run(tools["remember_memory"].ainvoke(
        {"name": "ci_notes", "content": "CI 先跑测试再发布。", "importance": 4}
    ))
    assert out.startswith("已记录到")


# --------------------------------------------------------------------- #
# N31 记忆「类型 + 硬容量」约束（CC 4 类 + Hermes 2200 字符封顶）
# --------------------------------------------------------------------- #
def test_remember_type_field_written_and_default(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    p_fact = memory.remember("fact_a", "服务器在北京机房。", type="fact")
    p_default = memory.remember("note_b", "普通记忆。")
    assert "type: fact" in p_fact.read_text(encoding="utf-8")
    assert "type: memory" in p_default.read_text(encoding="utf-8")


def test_remember_unknown_type_falls_back_to_memory(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    path = memory.remember("weird", "内容", type="自定义类型")
    assert "type: memory" in path.read_text(encoding="utf-8")


def test_remember_trims_content_to_hard_cap(tmp_path: Path) -> None:
    from chuan.memory import _MAX_DOC_CHARS

    memory = Memory(vault_path=tmp_path / "vault")
    long_body = "很长的内容。" * (_MAX_DOC_CHARS + 200)
    path = memory.remember("huge", long_body)
    raw = path.read_text(encoding="utf-8")
    body = raw.split("---\n\n", 1)[1]
    # 正文被硬截断到上限以内（不含 frontmatter）
    assert len(body.rstrip()) <= _MAX_DOC_CHARS
    # 覆盖写也遵守上限
    memory.remember("huge", "短内容")
    assert "短内容" in path.read_text(encoding="utf-8")


def test_recall_type_filter(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    memory.remember("fact_server", "服务器在北京机房。", type="fact")
    memory.remember("pref_lang", "用户偏好中文回复。", type="preference")
    memory.remember("proc_deploy", "部署流程：先测后发。", type="process")

    all_hits = memory.recall("服务器 机房 部署 偏好 中文")
    assert len(all_hits) == 3
    assert {h.type for h in all_hits} == {"fact", "preference", "process"}

    facts = memory.recall("服务器 机房 部署 偏好 中文", type="fact")
    assert len(facts) == 1 and facts[0].type == "fact"
    pref = memory.recall("服务器 机房 部署 偏好 中文", type="preference")
    assert pref and all(h.type == "preference" for h in pref)


def test_recall_hit_exposes_type(tmp_path: Path) -> None:
    memory = Memory(vault_path=tmp_path / "vault")
    memory.remember("fact_os", "系统是 Windows。", type="fact")
    hit = memory.recall("Windows 系统")[0]
    assert hit.type == "fact"
    assert hit.importance == 3


def test_memory_tools_type_param(tmp_path: Path) -> None:
    from chuan.memory_tools import build_memory_tools

    memory = Memory(vault_path=tmp_path / "vault")
    tools = {t.name: t for t in build_memory_tools(memory)}
    tools["remember_memory"].func("fact_region", "机房在北京。", type="fact")
    raw = (memory.vault_path / "notes" / "fact_region.md").read_text(encoding="utf-8")
    assert "type: fact" in raw
    recalled = tools["recall_memory"].func("机房", type="fact")
    assert "fact_region" in recalled
    # type 过滤：preference 类型召回不到 fact
    assert "没有找到" in tools["recall_memory"].func("机房", type="preference")


# ------------------------------------------------------------------ #
# N43 语义检索（sqlite-vec）：FTS5 词法 + 语义向量双路合并
# 用确定性 stub 嵌入（4 维，合同/契约同义、天气/晴同义），不触网、可复现。
# ------------------------------------------------------------------ #
def _stub_embed(texts: list[str]) -> list[list[float]]:
    """确定性 stub 嵌入：把「合同/契约」「天气/晴」映射到同义向量（4 维）。"""

    def vec(t: str) -> list[float]:
        if "合同" in t or "契约" in t:
            return [1.0, 0.0, 0.0, 0.0]
        if "天气" in t or "晴" in t:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]

    return [vec(t) for t in texts]


_stub_embed.dim = 4  # type: ignore[attr-defined]  # 让 _semantic_dim 读到维度


def test_semantic_disabled_by_default(tmp_path: Path) -> None:
    """默认（config enabled:false / 无注入）语义关闭：纯词法 FTS5 行为不变。"""
    memory = Memory(vault_path=tmp_path / "vault")
    assert memory._semantic_enabled() is False
    assert memory._ensure_vec() is None  # 未开启语义 → 不建向量表
    memory.remember("weather", "今天的武汉天气晴朗温暖。")
    assert memory.recall("武汉天气")[0].relative_path == "notes/weather.md"


def test_semantic_recall_finds_doc_fts_misses(tmp_path: Path) -> None:
    """语义补上词法漏掉的同义表达：query「契约」找不到含「合同」的文档时。"""
    plain = Memory(vault_path=tmp_path / "plain", embedding=False)
    plain.remember("contract", "本合同适用于所有场景。")
    # 纯词法：query「契约」与正文无共同 token → 召不到
    assert plain.recall("契约") == []

    semantic = Memory(vault_path=tmp_path / "sem", embedding=_stub_embed)
    semantic.remember("contract", "本合同适用于所有场景。")
    hits = semantic.recall("契约")
    assert [h.relative_path for h in hits] == ["notes/contract.md"]
    assert hits[0].score > 0


def test_semantic_merge_boosts_shared_hit(tmp_path: Path) -> None:
    """双路合并：同一文档词法+语义双命中 → 分数高于纯词法。"""
    with_sem = Memory(vault_path=tmp_path / "s1", embedding=_stub_embed)
    with_sem.remember("a", "今天天气晴朗。")
    s1 = with_sem.recall("天气")[0].score

    without = Memory(vault_path=tmp_path / "s2", embedding=False)
    without.remember("a", "今天天气晴朗。")
    s2 = without.recall("天气")[0].score

    assert s1 > s2 > 0  # 语义通道为 FTS 命中叠加了分数


def test_semantic_reindex_removes_stale_vec(tmp_path: Path) -> None:
    """reindex 删除文档时，向量索引同步清理（不残留脏条目）。"""
    memory = Memory(vault_path=tmp_path / "vault", embedding=_stub_embed)
    note = tmp_path / "vault" / "notes" / "manual.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("本合同相关记录。", encoding="utf-8")
    assert memory.reindex(namespace="notes") == 1
    assert memory.recall("契约") != []  # 语义已索引

    note.unlink()
    assert memory.reindex(namespace="notes") == 1
    assert memory.recall("契约") == []  # 向量条目已随删除清理


def test_semantic_respects_importance_gate(tmp_path: Path) -> None:
    """语义命中同样过 importance 门控。"""
    memory = Memory(vault_path=tmp_path / "vault", embedding=_stub_embed)
    memory.remember("contract", "本合同适用。", importance=1)
    # 语义能找到，但 importance 门控过滤掉
    assert memory.recall("契约", min_importance=3) == []
    assert memory.recall("契约", min_importance=1) != []
