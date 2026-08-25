"""N24 wiki 知识库维护层测试：实体归并 / index+log / raw 只读 / reconcile / lint。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chuan.memory import Memory
from chuan.memory_tools import build_wiki_tools
from chuan.wiki import Wiki


@pytest.fixture
def wiki(tmp_path: Path) -> Wiki:
    memory = Memory(vault_path=tmp_path / "vault")
    w = Wiki(memory)
    w.ensure_dirs()
    return w


def test_write_creates_new_entity_page(wiki: Wiki) -> None:
    path = wiki.write("topics", "知识库搭建", "第一性原理：编译而非检索。")
    assert path.name == "知识库搭建.md"
    assert path.parent.name == "topics"
    text = path.read_text(encoding="utf-8")
    assert "confidence: 3" in text
    assert "知识库搭建" in text


def test_write_rejects_readonly_sources(wiki: Wiki) -> None:
    with pytest.raises(ValueError):
        wiki.write("sources", "raw", "不许直接写")


def test_write_unknown_namespace_rejected(wiki: Wiki) -> None:
    with pytest.raises(ValueError):
        wiki.write("hack", "x", "y")


def test_write_merges_same_entity_section(wiki: Wiki) -> None:
    first = wiki.write("analysis", "RAG选型", "结论：小规模不用向量。", source="src-a")
    created = first.read_text(encoding="utf-8").splitlines()[2]

    # 第二次写同名小节 → 覆盖旧声明 + 保留 created + 标注 deprecated
    wiki.write("analysis", "RAG选型", "结论：规模上千才上向量。", source="src-b")
    text = first.read_text(encoding="utf-8")
    assert created in text  # created 保留
    assert "结论：规模上千才上向量" in text  # 新声明
    assert "旧结论" in text and "deprecated" in text  # 旧声明留痕
    # 同一小节只出现一次（覆盖而非追加）
    assert text.count("## RAG选型") == 1


def test_write_appends_new_section(wiki: Wiki) -> None:
    wiki.write("entities", "obsidian-second-brain", "它是一个 skill。", section="定位")
    wiki.write(
        "entities",
        "obsidian-second-brain",
        "矛盾自动调和。",
        section="机制",
    )
    text = (wiki.namespace_path("entities") / "obsidian-second-brain.md").read_text(
        encoding="utf-8"
    )
    assert "## 定位" in text and "## 机制" in text
    assert "矛盾自动调和" in text


def test_import_source_appends_not_overwrites(wiki: Wiki) -> None:
    p = wiki.import_source("对话A", "原始内容一", source="session:a")
    assert p.parent.name == "sources"
    wiki.import_source("对话A", "补充内容二", source="session:a")
    text = p.read_text(encoding="utf-8")
    assert "原始内容一" in text
    assert "补充内容二" in text
    assert text.count("# 对话A") == 1  # 不重复建页


def test_index_refreshed_and_searchable(wiki: Wiki) -> None:
    wiki.write("topics", "RAG", "什么时候不用向量", source="a")
    index = wiki.namespace_path("topics") / "index.md"
    assert "[[topics/RAG]]" in index.read_text(encoding="utf-8")

    hits = wiki.search_index("RAG")
    assert hits and hits[0]["rel_path"] == "topics/RAG.md"
    assert wiki.search_index("不存在的词") == []


def test_log_appends_on_write(wiki: Wiki) -> None:
    wiki.write("projects", "N24", "记忆层升级", source="plan")
    log = wiki.notes_path / "log.md"
    text = log.read_text(encoding="utf-8")
    assert "projects/N24.md" in text
    assert "write" in text


def test_reconcile_reports_superseded(wiki: Wiki) -> None:
    wiki.write("analysis", "结论", "旧结论 A", section="核心", source="old")
    wiki.write("analysis", "结论", "新结论 B", section="核心", source="new")
    report = wiki.reconcile("analysis", "结论")
    assert report["exists"] is True
    assert report["verdict"] == "superseded"
    assert report["deprecated_count"] >= 1
    assert "核心" in report["current_sections"]


def test_lint_detects_orphans_deadlinks_missing_meta(wiki: Wiki) -> None:
    # 有出链指向不存在的页面 → dead_link；自身无入链无出链 → orphan
    wiki.write("topics", "甲", "内容链接到 [[不存在的页]]", source="s")
    wiki.write("topics", "乙", "孤立内容无链接", source="s")
    report = wiki.lint()
    assert report["pages"] == 2
    assert "topics/乙.md" in report["orphans"]
    assert any("不存在的页" in d for d in report["dead_links"])
    # 经 write 写入的页都有 frontmatter（importance/confidence 必填）
    assert report["missing_meta"] == []


def test_build_wiki_tools_exposes_two_tools(wiki: Wiki) -> None:
    tools = build_wiki_tools(wiki.memory)
    names = {t.name for t in tools}
    assert names == {"wiki_write", "wiki_search"}


def test_consolidation_sources_landing(wiki: Wiki, tmp_path: Path) -> None:
    """consolidate_sessions 传 wiki 时，蒸馏产物落到 sources/ 而非 notes/。"""
    # import_source 已把内容写进 sources/，且 notes/ 根不产生 session-*.md
    wiki.import_source("session-fake", "蒸馏内容", source="session:fake")
    sources = tmp_path / "vault" / "notes" / "sources" / "session-fake.md"
    assert sources.exists()
    assert "蒸馏内容" in sources.read_text(encoding="utf-8")
    # notes/ 根目录不应出现 session-*.md（落点已迁移）
    assert not (tmp_path / "vault" / "notes" / "session-fake.md").exists()


def test_ingest_deterministic_routes_sections(wiki: Wiki) -> None:
    """无 LLM 时确定性归位：结论→analysis/，待办→projects/，并回链 raw 源。"""
    wiki.import_source(
        "session-abc",
        "## 结论\n- 小规模知识库不需要向量检索\n\n"
        "## 关键细节\n- FTS5 足够\n\n## 待办/后续\n- 规模上千后再评估向量\n",
        source="session:abc",
    )
    report = wiki.ingest_sources(use_llm=False)
    assert "session-abc.md" in report["ingested"]

    analysis = wiki.namespace_path("analysis") / "abc.md"
    projects = wiki.namespace_path("projects") / "abc.md"
    assert analysis.exists()
    assert projects.exists()
    a_text = analysis.read_text(encoding="utf-8")
    p_text = projects.read_text(encoding="utf-8")
    assert "小规模知识库不需要向量检索" in a_text
    assert "[[sources/session-abc]]" in a_text  # 回链 raw 源
    assert "规模上千后再评估向量" in p_text


def test_ingest_is_idempotent_and_flagged(wiki: Wiki) -> None:
    """整理过的原料打 wiki_ingested 标记，二次 ingest 跳过不再重复写页。"""
    wiki.import_source("session-x", "## 结论\n- 结论甲", source="session:x")
    first = wiki.ingest_sources(use_llm=False)
    assert len(first["ingested"]) == 1

    src = wiki.namespace_path("sources") / "session-x.md"
    assert "wiki_ingested: true" in src.read_text(encoding="utf-8")

    second = wiki.ingest_sources(use_llm=False)
    assert second["ingested"] == [] and "session-x.md" in second["skipped"]
    # 未重复生成实体页（同名归并；排除 index.md）
    pages = [p for p in wiki.namespace_path("analysis").glob("*.md") if p.name != "index.md"]
    assert len(pages) == 1


def test_ingest_append_clears_flag_for_reingest(wiki: Wiki) -> None:
    """import_source 追加新内容会清标记，下次 ingest 重新归位新增部分。"""
    wiki.import_source("session-y", "## 结论\n- 旧结论", source="session:y")
    wiki.ingest_sources(use_llm=False)

    wiki.import_source("session-y", "## 结论\n- 新结论", source="session:y")
    src = wiki.namespace_path("sources") / "session-y.md"
    assert "wiki_ingested: true" not in src.read_text(encoding="utf-8")

    wiki.ingest_sources(use_llm=False)
    analysis = wiki.namespace_path("analysis") / "y.md"
    text = analysis.read_text(encoding="utf-8")
    assert "新结论" in text  # 新声明已归位
    assert "旧结论" in text  # 旧声明 deprecated 留痕


class _FakeBrain:
    """模拟 Brain.complete：返回预置 JSON 路由结果。"""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, messages, **kwargs) -> str:  # noqa: ANN001
        return self._reply


def test_ingest_llm_routing_writes_entity_pages(wiki: Wiki) -> None:
    """LLM 路径：按模型返回的 JSON 路由到对应实体页。"""
    wiki.import_source("session-z", "关于 RAG 的讨论。", source="session:z")
    brain = _FakeBrain(
        '[{"entity_type": "analysis", "entity_name": "RAG选型", '
        '"content": "小规模不用向量", "importance": 4, "confidence": 5}]'
    )
    report = wiki.ingest_sources(brain=brain, use_llm=True)
    assert report["llm_routed"] == 1
    page = wiki.namespace_path("analysis") / "RAG选型.md"
    assert page.exists()
    text = page.read_text(encoding="utf-8")
    assert "小规模不用向量" in text
    assert "confidence: 5" in text


def test_ingest_llm_failure_falls_back_to_deterministic(wiki: Wiki) -> None:
    """LLM 输出退化/不可解析时回退确定性归位，不丢内容。"""
    wiki.import_source("session-w", "## 结论\n- 保底结论", source="session:w")
    brain = _FakeBrain("抱歉，我不会 JSON。")
    report = wiki.ingest_sources(brain=brain, use_llm=True)
    assert report["llm_routed"] == 0
    page = wiki.namespace_path("analysis") / "w.md"
    assert page.exists()
    assert "保底结论" in page.read_text(encoding="utf-8")
