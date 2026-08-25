"""N2 Skill 加载器测试 —— 注册表与工具解析。"""

from __future__ import annotations

from pathlib import Path

import yaml

from chuan.adapters.skill_loader import SkillRegistry, ToolRegistry


def _make_skill_yaml(tmp_path: Path, name: str, **overrides: object) -> Path:
    dir_path = tmp_path / "skills"
    dir_path.mkdir(exist_ok=True)
    data: dict[str, object] = {"name": name, "description": f"skill {name}"}
    data.update(overrides)
    path = dir_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return dir_path


def test_skill_registry_loads_all(tmp_path: Path) -> None:
    _make_skill_yaml(tmp_path, "weather_check", mcp_server="weather")
    _make_skill_yaml(tmp_path, "code_execution", mcp_server="opencode")
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    names = registry.list_all()
    assert "weather_check" in names
    assert "code_execution" in names


def test_skill_registry_get(tmp_path: Path) -> None:
    _make_skill_yaml(tmp_path, "test_skill", mcp_server="test")
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    skill = registry.get("test_skill")
    assert skill is not None
    assert skill.name == "test_skill"


def test_skill_registry_get_returns_none(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    assert registry.get("nonexistent") is None


def test_skill_registry_skips_malformed_yaml(tmp_path: Path) -> None:
    dir_path = tmp_path / "skills"
    dir_path.mkdir()
    (dir_path / "bad.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
    _make_skill_yaml(tmp_path, "good_skill", mcp_server="ok")
    registry = SkillRegistry(skills_dir=dir_path)
    assert "good_skill" in registry.list_all()


def test_skill_mcp_type(tmp_path: Path) -> None:
    _make_skill_yaml(tmp_path, "weather", mcp_server="weather")
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    skill = registry.get("weather")
    assert skill is not None
    assert skill.kind == "mcp"
    assert skill.target == "weather"


def test_skill_handler_type(tmp_path: Path) -> None:
    _make_skill_yaml(
        tmp_path,
        "scan",
        handler={"module": "handlers.legal_scan", "function": "scan_contract"},
    )
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    skill = registry.get("scan")
    assert skill is not None
    assert skill.kind == "handler"


def test_skill_prompt_type(tmp_path: Path) -> None:
    _make_skill_yaml(tmp_path, "template", prompt="你是一个助手")
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    skill = registry.get("template")
    assert skill is not None
    assert skill.kind == "prompt"


def test_skill_registry_list_mcp_dependencies(tmp_path: Path) -> None:
    _make_skill_yaml(tmp_path, "weather", mcp_server="weather")
    _make_skill_yaml(tmp_path, "code", mcp_server="opencode")
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    deps = registry.list_mcp_dependencies()
    assert "weather" in deps
    assert "opencode" in deps


def test_skill_registry_list_mcp_dependencies_with_deny(tmp_path: Path) -> None:
    _make_skill_yaml(tmp_path, "weather", mcp_server="weather")
    _make_skill_yaml(tmp_path, "code", mcp_server="opencode")
    registry = SkillRegistry(skills_dir=tmp_path / "skills")
    deps = registry.list_mcp_dependencies(deny=["weather"])
    assert "weather" not in deps
    assert "opencode" in deps


def test_skill_registry_empty_directory(tmp_path: Path) -> None:
    dir_path = tmp_path / "skills"
    dir_path.mkdir()
    registry = SkillRegistry(skills_dir=dir_path)
    assert registry.list_all() == []


def test_skill_registry_nonexistent_directory(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_dir=tmp_path / "nonexistent")
    assert registry.list_all() == []


def test_tool_registry_get_tools_with_deny(tmp_path: Path) -> None:
    _make_skill_yaml(
        tmp_path,
        "contract_review",
        handler={"module": "handlers.legal_scan", "function": "scan_contract"},
    )
    skill_registry = SkillRegistry(skills_dir=tmp_path / "skills")
    tool_registry = ToolRegistry(skill_registry, mcp_adapter=None)
    tools = tool_registry.get_tools(deny=["contract_review"])
    assert tools == []


def test_tool_registry_list_all_sources(tmp_path: Path) -> None:
    _make_skill_yaml(
        tmp_path,
        "contract_review",
        handler={"module": "handlers.legal_scan", "function": "scan_contract"},
    )
    skill_registry = SkillRegistry(skills_dir=tmp_path / "skills")
    tool_registry = ToolRegistry(skill_registry, mcp_adapter=None)
    sources = tool_registry.list_all_sources()
    assert "skills" in sources
    assert "contract_review" in sources["skills"]