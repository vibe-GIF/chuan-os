"""L3 从做到造 —— 知识原子（howto）测试：保存/检索/自动复用注入/工具暴露。"""

from __future__ import annotations

from pathlib import Path

from chuan.howto import HowToStore
from chuan.memory import Memory
from chuan.memory_tools import build_howto_tools
from chuan.role import Department


def _memory(tmp_path: Path) -> Memory:
    return Memory(vault_path=tmp_path / "vault")


def test_save_creates_atom_with_trigger_and_tools(tmp_path: Path) -> None:
    store = HowToStore(_memory(tmp_path))
    path = store.save(
        "部署周报",
        "每周五 汇总部署 周报",
        "1. 汇总本周部署变更\n2. 列出影响范围\n3. 生成周报",
        tools="pi, bash",
    )
    assert path == tmp_path / "vault" / "notes" / "howto" / "部署周报.md"
    text = path.read_text(encoding="utf-8")
    # trigger 在正文小节，tools 进 frontmatter tags（wiki 第 6 类命名空间）
    assert "## 怎么做" in text and "## 触发场景" in text
    assert "每周五 汇总部署 周报" in text
    assert "1. 汇总本周部署变更" in text
    assert "bash" in text and "pi" in text


def test_howto_gets_wiki_index_and_lint(tmp_path: Path) -> None:
    """并入 wiki 后：howto 有 index.md 索引、lint 健康检查覆盖、wiki_search 可定位。"""
    from chuan.wiki import Wiki

    store = HowToStore(_memory(tmp_path))
    store.save("部署周报", "每周五 汇总部署 周报", "生成周报", source="s")

    index = tmp_path / "vault" / "notes" / "howto" / "index.md"
    assert index.exists()
    assert "[[howto/部署周报]]" in index.read_text(encoding="utf-8")

    # lint 覆盖 howto 页（有 frontmatter，不报缺元数据）
    report = Wiki(store.memory).lint()
    assert "howto/部署周报.md" not in report["missing_meta"]

    # wiki_search 能在 howto 索引里定位
    hits = Wiki(store.memory).search_index("部署周报")
    assert hits and hits[0]["rel_path"] == "howto/部署周报.md"


def test_save_same_name_updates_not_duplicates(tmp_path: Path) -> None:
    store = HowToStore(_memory(tmp_path))
    p1 = store.save("写代码", "写 python 代码", "第一步", source="a")
    created = p1.read_text(encoding="utf-8").splitlines()[2]
    store.save("写代码", "写 python 代码", "第二步（更新）", source="b")
    text = p1.read_text(encoding="utf-8")
    assert text.count("# 写代码") == 1  # 同名更新不重复建页
    assert created in text  # created 保留
    assert "第二步（更新）" in text


def test_find_recalls_by_trigger_and_content(tmp_path: Path) -> None:
    store = HowToStore(_memory(tmp_path))
    store.save("部署周报", "每周五 汇总部署", "生成周报", source="s")
    store.save("文档整理", "Obsidian 笔记 归档", "三步归位", source="s")

    hits = store.find("部署 周报")
    assert hits and hits[0]["name"] == "部署周报"
    assert hits[0]["trigger"] == "每周五 汇总部署"

    other = store.find("Obsidian 归档")
    assert other and other[0]["name"] == "文档整理"


def test_suggest_strong_hit_returns_reference(tmp_path: Path) -> None:
    store = HowToStore(_memory(tmp_path))
    store.save("部署周报", "每周五 汇总部署 周报", "1. 汇总变更\n2. 生成周报", source="s")
    ref = store.suggest("帮我部署周报，周五要发")
    assert ref is not None
    assert "部署周报" in ref and "参考做法" in ref


def test_suggest_weak_no_hit_returns_none(tmp_path: Path) -> None:
    store = HowToStore(_memory(tmp_path))
    store.save("部署周报", "每周五 汇总部署", "生成周报", source="s")
    assert store.suggest("今天天气怎么样") is None  # 无命中 → 不注入


def test_suggest_min_score_filters_noise(tmp_path: Path) -> None:
    store = HowToStore(_memory(tmp_path))
    store.save("部署周报", "部署 发布", "发布步骤", source="s")
    # 提高阈值后弱命中被过滤
    assert store.suggest("部署", min_score=999.0) is None


def test_build_howto_tools_exposes_three(tmp_path: Path) -> None:
    tools = build_howto_tools(_memory(tmp_path))
    assert {t.name for t in tools} == {"howto_save", "howto_find", "howto_show"}


def test_role_inject_howto_when_memory_present(tmp_path: Path) -> None:
    from types import SimpleNamespace

    memory = _memory(tmp_path)
    HowToStore(memory).save(
        "部署周报", "每周五 汇总部署 周报", "1. 汇总变更\n2. 生成周报", source="s"
    )
    persona = SimpleNamespace(name="worker", display_name="工", description="")
    role = Department(persona, agent_pool=None, memory=memory)

    out = role._maybe_inject_howto("帮我部署周报，周五要发")
    assert "参考做法" in out and "部署周报" in out and "帮我部署周报" in out

    # 无命中 → 原样返回
    assert role._maybe_inject_howto("今天天气怎么样") == "今天天气怎么样"


def test_role_inject_howto_no_memory_unchanged(tmp_path: Path) -> None:
    from types import SimpleNamespace

    persona = SimpleNamespace(name="worker", display_name="工", description="")
    role = Department(persona, agent_pool=None, memory=None)
    assert role._maybe_inject_howto("随便什么任务") == "随便什么任务"
