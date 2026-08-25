"""外接只读库（Obsidian）接入 FTS5 测试：多 vault key 隔离 / 只读 / 跨库召回。

覆盖：config 解析、reindex_external（跳过隐藏目录）、增量与删除同步、
recall(vaults=...) 显式跨库召回且默认不混入内部管道、外部库只读。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chuan.memory import Memory


@pytest.fixture
def ext_vault(tmp_path: Path) -> tuple[Memory, Path]:
    """构造一个指向临时外部库的 Memory（config 声明 external_vaults）。"""
    external = tmp_path / "ext"
    (external / "topic").mkdir(parents=True)
    (external / "topic" / "A.md").write_text(
        "# 向量检索\n\nRAG 向量 语义检索 关键词 测试。\n", encoding="utf-8"
    )
    (external / "B.md").write_text(
        "# 全文索引\n\nFTS5 中文 拆分 测试。\n", encoding="utf-8"
    )
    # 隐藏目录（.obsidian/.trash）应被跳过
    (external / ".obsidian").mkdir()
    (external / ".obsidian" / "app.md").write_text("# 配置\n\n隐藏内容。\n", encoding="utf-8")
    (external / ".trash").mkdir()
    (external / ".trash" / "old.md").write_text("# 废弃\n\n旧内容。\n", encoding="utf-8")

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "memory:\n  external_vaults:\n"
        f"    - name: obsidian\n      path: '{str(external).replace(chr(92), '/')}'\n",
        encoding="utf-8",
    )
    memory = Memory(vault_path=tmp_path / "vault", config_path=cfg)
    return memory, external


def test_external_vaults_loaded_from_config(ext_vault: tuple[Memory, Path]) -> None:
    memory, external = ext_vault
    assert memory._load_external_vaults() == [("obsidian", external.resolve())]


def test_reindex_external_indexes_and_skips_hidden(ext_vault: tuple[Memory, Path]) -> None:
    memory, external = ext_vault
    report = memory.reindex_external()
    assert report == {"obsidian": 2}  # 只含 A.md + B.md，隐藏目录被跳过

    hits = memory._fts_candidate_paths(
        memory._tokens("向量"), root=external, vault_key=memory._vault_key_for(external)
    )
    assert hits == {external / "topic" / "A.md"}

    # 内部 vault 的候选不受外部库影响（vault key 隔离）
    internal_hits = memory._fts_candidate_paths(["向量"])
    assert not internal_hits or not any(
        external in p.parents for p in internal_hits
    )


def test_recall_external_only_when_requested(ext_vault: tuple[Memory, Path]) -> None:
    memory, _ = ext_vault
    memory.reindex_external()

    # 默认召回不混入外部库：内部无命中时返回空
    memory.remember("internal", "内部 关键词 记忆。")
    assert memory.recall("向量") == []

    # 显式 vaults 才跨库召回，命中路径相对外部库根
    hits = memory.recall("向量", vaults=["obsidian"])
    assert [h.relative_path for h in hits] == ["topic/A.md"]
    assert "语义检索" in hits[0].content


def test_recall_external_default_importance(ext_vault: tuple[Memory, Path]) -> None:
    """外部文档无 frontmatter 时按 importance=3 处理，min_importance 门控不误杀。"""
    memory, _ = ext_vault
    memory.reindex_external()
    hits = memory.recall("向量", vaults=["obsidian"], min_importance=3)
    assert [h.relative_path for h in hits] == ["topic/A.md"]


def test_reindex_external_incremental_and_stale(ext_vault: tuple[Memory, Path]) -> None:
    memory, external = ext_vault
    assert memory.reindex_external() == {"obsidian": 2}
    # mtime 未变 → 增量 0
    assert memory.reindex_external() == {"obsidian": 0}

    # 修改 B.md（强制 mtime 前进，避免文件系统时间戳粒度）→ 只重建该条
    b = external / "B.md"
    b.write_text("# 全文索引\n\nFTS5 中文 拆分 修订 测试。\n", encoding="utf-8")
    os.utime(b, (1_700_000_100, 1_700_000_100))
    assert memory.reindex_external() == {"obsidian": 1}

    # 删除 A.md → 清除索引残留（meta 无脏行）
    (external / "topic" / "A.md").unlink()
    assert memory.reindex_external() == {"obsidian": 1}
    vault_key = memory._vault_key_for(external)
    n = memory._ensure_fts().execute(
        "SELECT COUNT(*) FROM memory_meta WHERE vault=?", (vault_key,)
    ).fetchone()[0]
    assert n == 1  # 只剩 B.md


def test_external_vault_readonly(ext_vault: tuple[Memory, Path]) -> None:
    """索引外部库绝不写回：文件内容保持原样（无 frontmatter 注入）。"""
    memory, external = ext_vault
    a = external / "topic" / "A.md"
    before = a.read_bytes()
    memory.reindex_external()
    assert a.read_bytes() == before
    assert "# 向量检索" in a.read_text(encoding="utf-8")
