"""P3 向量 RAG 评估闸门（skills/handlers/rag_gate.py）测试。

确定性统计 + 阈值判定；不触网/不触模型；用 tmp_path 注入 vault，
monkeypatch 阈值调小以快速构造触发态。
"""

from __future__ import annotations

from pathlib import Path

from skills.handlers import rag_gate as rg

from chuan.adapters.skill_loader import SkillRegistry


def _mk_notes(vault: Path, docs: int, size: int = 200) -> Path:
    """在 vault/notes 下造 docs 篇 .md，每篇 size 字节。返回 notes 目录。"""
    notes = vault / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    for i in range(docs):
        (notes / f"doc_{i}.md").write_text("x" * size, encoding="utf-8")
    return notes


# --------------------------------------------------------------------- #
# skill 注册
# --------------------------------------------------------------------- #
def test_rag_gate_skill_registered() -> None:
    registry = SkillRegistry()
    skill = registry.get("rag_gate")
    assert skill is not None
    assert skill.kind == "handler"
    tool = skill.to_tool()
    assert tool is not None and tool.name == "rag_gate"


def test_rag_gate_has_trigger_keywords() -> None:
    registry = SkillRegistry()
    skill = registry.get("rag_gate")
    assert "漏召回" in skill.trigger.get("keywords", [])
    assert skill.matches("记忆库多大")


# --------------------------------------------------------------------- #
# 规模未达阈值 → 未触发
# --------------------------------------------------------------------- #
def test_empty_vault_not_hit(tmp_path) -> None:
    out = rg.rag_gate(str(tmp_path))
    assert rg._GATE_NOT_HIT in out
    assert "未达阈值" in out


def test_small_vault_not_hit(tmp_path) -> None:
    _mk_notes(tmp_path, docs=3, size=50)
    out = rg.rag_gate(str(tmp_path))
    assert rg._GATE_NOT_HIT in out
    assert "3 篇" in out


# --------------------------------------------------------------------- #
# 规模达标 + 无案例 → 待案例；有案例 → 触发
# --------------------------------------------------------------------- #
def test_size_hit_without_case_partial(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rg, "_DOC_THRESHOLD", 2)
    monkeypatch.setattr(rg, "_CHAR_THRESHOLD", 100)
    _mk_notes(tmp_path, docs=3, size=200)  # 3 篇 / 600 字符 > 阈值
    out = rg.rag_gate(str(tmp_path))
    assert rg._GATE_PARTIAL in out
    assert "待案例" in out
    assert "0 条" in out


def test_size_hit_with_case_triggers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rg, "_DOC_THRESHOLD", 2)
    monkeypatch.setattr(rg, "_CHAR_THRESHOLD", 100)
    _mk_notes(tmp_path, docs=3, size=200)
    rg.record_missed_case("关键词A", "FTS 漏召回", vault_path=tmp_path)
    out = rg.rag_gate(str(tmp_path))
    assert rg._GATE_HIT in out
    assert "触发" in out
    assert "1 条" in out


# --------------------------------------------------------------------- #
# 外接库并入合计
# --------------------------------------------------------------------- #
def test_external_vault_counts_into_total(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rg, "_DOC_THRESHOLD", 2)
    monkeypatch.setattr(rg, "_CHAR_THRESHOLD", 100)
    _mk_notes(tmp_path, docs=2, size=200)  # 内部 2 篇
    ext = tmp_path / "ext"
    _mk_notes(ext, docs=3, size=100)  # 外接 3 篇
    monkeypatch.setattr(
        rg, "_resolve_external_vaults",
        lambda cfg="": [("obsidian", ext)],
    )
    out = rg.rag_gate(str(tmp_path))
    assert "外接库「obsidian」" in out
    assert "5 篇" in out  # 2 + 3


# --------------------------------------------------------------------- #
# 漏召回案例记录
# --------------------------------------------------------------------- #
def test_record_missed_case_appends(tmp_path) -> None:
    msg = rg.record_missed_case("query", "note", vault_path=tmp_path)
    assert "已记录" in msg
    cases = rg._count_cases(rg._cases_path(tmp_path))
    assert cases == 1
    rg.record_missed_case("query2", vault_path=tmp_path)
    assert rg._count_cases(rg._cases_path(tmp_path)) == 2


def test_count_cases_missing_file_zero(tmp_path) -> None:
    assert rg._count_cases(rg._cases_path(tmp_path)) == 0


# --------------------------------------------------------------------- #
# 静默降级
# --------------------------------------------------------------------- #
def test_missing_notes_dir_degrades(tmp_path) -> None:
    out = rg.rag_gate(str(tmp_path / "nonexistent"))
    assert rg._GATE_NOT_HIT in out
    assert "0 篇" in out


def test_default_vault_runs_without_error() -> None:
    """不传参时跑真实默认库：只要求不抛错、返回可读报告。"""
    out = rg.rag_gate()
    assert "向量 RAG 评估闸门" in out
