"""N20 GEPA 自改进循环的单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from chuan.self_improve.gepa import assess, preserve, run_gepa


def _dir_persona(tmp_path) -> SimpleNamespace:
    """构造一个目录格式（ADR-013）角色，directory 指向临时目录。"""
    return SimpleNamespace(name="researcher", directory=tmp_path / "researcher")


def _yaml_persona() -> SimpleNamespace:
    """构造一个旧 YAML 格式角色（无 directory）。"""
    return SimpleNamespace(name="lawyer", directory=None)


def test_assess_success_contains_mark() -> None:
    lesson = assess("调研竞品", "结论：竞品主打价格战", success=True)
    assert lesson is not None
    assert "完成" in lesson
    assert "调研竞品" in lesson
    assert "价格战" in lesson


def test_assess_failure_marks_unfinished() -> None:
    lesson = assess("写脚本", "报错：权限不足", success=False)
    assert lesson is not None
    assert "未完成" in lesson
    assert "权限不足" in lesson


def test_assess_none_for_empty_content() -> None:
    assert assess("任务", "", success=True) is None
    assert assess("任务", "   \n  ", success=True) is None


def test_assess_truncates_long_content() -> None:
    lesson = assess("汇报", "x" * 500, success=True)
    assert lesson is not None
    # 摘要截断到上限，不会把 500 字原样塞进记忆
    assert len(lesson) < 300


def test_preserve_writes_memory_md(tmp_path) -> None:
    persona = _dir_persona(tmp_path)
    path = preserve(persona, "- 一条经验")
    assert path is not None
    memory_file = persona.directory / "MEMORY.md"
    assert memory_file.exists()
    assert "- 一条经验" in memory_file.read_text(encoding="utf-8")


def test_preserve_skips_non_directory() -> None:
    assert preserve(_yaml_persona(), "- 经验") is None


def test_run_gepa_skips_non_directory() -> None:
    assert run_gepa(_yaml_persona(), "任务", "结论", success=True) is False


def test_run_gepa_preserves_for_directory(tmp_path) -> None:
    persona = _dir_persona(tmp_path)
    assert run_gepa(persona, "查天气", "武汉今日 20 度", success=True) is True
    content = (persona.directory / "MEMORY.md").read_text(encoding="utf-8")
    assert "查天气" in content
    assert "武汉" in content


def test_run_gepa_no_content_returns_false(tmp_path) -> None:
    persona = _dir_persona(tmp_path)
    assert run_gepa(persona, "任务", "", success=True) is False
    assert not (persona.directory / "MEMORY.md").exists()