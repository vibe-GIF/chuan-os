"""N3 persona 出生测试 —— YAML 解析与 agent 出生。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from chuan.persona_loader import Persona, PersonaLoader


# ------------------------------------------------------------------ #
# Persona 静态配置测试
# ------------------------------------------------------------------ #
def test_persona_legacy_allowlist_format() -> None:
    p = Persona(
        "lawyer",
        {
            "display_name": "律师",
            "description": "合同审查",
            "brain": "cloud_general",
            "tools": ["filesystem", "memory"],
            "skills": ["contract_review"],
        },
    )
    assert p.uses_legacy_allowlist
    assert "filesystem" in p.legacy_allowlist
    assert "contract_review" in p.legacy_allowlist


def test_persona_adr009_deny_format() -> None:
    p = Persona(
        "bodyguard",
        {
            "display_name": "保镖",
            "description": "安全审查",
            "role": "security",
            "deny": ["code_execution", "opencode"],
        },
    )
    assert not p.uses_legacy_allowlist
    assert p.deny == ["code_execution", "opencode"]
    assert p.role == "security"


def test_persona_deny_defaults_to_empty() -> None:
    p = Persona("test", {})
    assert p.deny == []


def test_memory_tools_injected_when_memory_set(
    tmp_path: Path,
) -> None:
    from chuan.memory import Memory

    loader = PersonaLoader(memory=Memory(vault_path=tmp_path / "vault"))
    loader._personas["probe"] = Persona(
        "probe", {"brain": "cloud_general", "role": "probe"}
    )
    names = [t.name for t in loader._resolve_tools(loader._personas["probe"])]
    assert "remember_memory" in names
    assert "recall_memory" in names

    # 未注入 memory 时不出现记忆工具
    loader_without = PersonaLoader()
    loader_without._personas["probe"] = Persona(
        "probe", {"brain": "cloud_general", "role": "probe"}
    )
    names2 = [t.name for t in loader_without._resolve_tools(loader_without._personas["probe"])]
    assert "remember_memory" not in names2


def _write_dir_persona(root: Path, name: str, config: dict[str, Any], soul: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    (d / "SOUL.md").write_text(soul, encoding="utf-8")


def test_directory_format_persona_loading(tmp_path: Path) -> None:
    root = tmp_path / "personas"
    _write_dir_persona(
        root, "architect",
        {"name": "architect", "display_name": "架构师", "brain": "cloud_general", "role": "design"},
        "我偏好分层架构，重视可维护性。",
    )
    loader = PersonaLoader(personas_dir=root)
    persona = loader.get_persona("architect")
    assert persona is not None
    assert persona.brain == "cloud_general"
    assert persona.role == "design"
    assert persona.directory == root / "architect"
    assert persona.soul == "我偏好分层架构，重视可维护性。"
    # SOUL 注入 system prompt
    assert "我偏好分层架构，重视可维护性" in persona.build_system_prompt()


def test_directory_does_not_override_yaml(tmp_path: Path) -> None:
    root = tmp_path / "personas"
    root.mkdir(parents=True, exist_ok=True)
    (root / "lawyer.yaml").write_text(
        yaml.safe_dump({"name": "lawyer", "display_name": "律师", "brain": "cloud_general", "role": "law"},
                       allow_unicode=True),
        encoding="utf-8",
    )
    _write_dir_persona(root, "lawyer", {"name": "lawyer", "display_name": "YAML优先", "brain": "cloud_general", "role": "x"}, "")
    loader = PersonaLoader(personas_dir=root)
    persona = loader.get_persona("lawyer")
    assert persona is not None
    # .yaml 为准，目录格式不覆盖
    assert persona.directory is None
    assert persona.display_name == "律师"


def test_role_memory_tools_bound_to_directory(tmp_path: Path) -> None:
    from chuan.memory_tools import build_role_memory_tools

    role_dir = tmp_path / "role"
    role_dir.mkdir(exist_ok=True)
    tools = {t.name: t for t in build_role_memory_tools(role_dir)}

    assert "read_role_memory" in tools
    assert "append_role_memory" in tools

    # 空时读回提示
    assert "暂无私有记忆" in tools["read_role_memory"].func()

    # 追加一条后能读回
    tools["append_role_memory"].func("合同审查：先核对签约主体。")
    text = tools["read_role_memory"].func()
    assert "先核对签约主体" in text
    assert (role_dir / "MEMORY.md").exists()


def test_persona_build_system_prompt() -> None:
    p = Persona(
        "lawyer",
        {
            "display_name": "律师",
            "description": "合同审查、法律咨询",
            "prompt": "引用中国法律条文。",
        },
    )
    prompt = p.build_system_prompt()
    assert "律师" in prompt
    assert "合同审查" in prompt
    assert "引用中国法律条文" in prompt
    assert "幕僚长" in prompt


def test_persona_build_system_prompt_with_can_dispatch() -> None:
    p = Persona(
        "chief",
        {
            "display_name": "幕僚长",
            "description": "总调度",
            "can_dispatch_to": ["lawyer", "programmer"],
        },
    )
    prompt = p.build_system_prompt()
    assert "lawyer" in prompt
    assert "programmer" in prompt


def test_persona_repr() -> None:
    p = Persona("test", {"deny": ["foo"]})
    assert "test" in repr(p)


# ------------------------------------------------------------------ #
# PersonaLoader 测试
# ------------------------------------------------------------------ #
def _make_persona_yaml(tmp_path: Path, name: str, **overrides: Any) -> Path:
    dir_path = tmp_path / "personas"
    dir_path.mkdir(exist_ok=True)
    data: dict[str, Any] = {
        "name": name,
        "display_name": name,
        "description": f"description for {name}",
        "brain": "cloud_general",
    }
    data.update(overrides)
    path = dir_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return dir_path


def test_persona_loader_loads_yaml_files(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    assert "test_agent" in loader.list_personas()


def test_persona_loader_skips_malformed_yaml(tmp_path: Path) -> None:
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    (dir_path / "bad.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
    _make_persona_yaml(tmp_path, "good_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    assert "good_agent" in loader.list_personas()


def test_persona_loader_birth_returns_compiled_graph(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    agent = loader.birth("test_agent")
    assert agent.name == "test_agent"


def test_persona_loader_birth_caches_agent(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    a1 = loader.birth("test_agent")
    a2 = loader.birth("test_agent")
    assert a1 is a2


def test_persona_loader_force_rebirth(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    a1 = loader.birth("test_agent")
    a2 = loader.birth("test_agent", force_rebirth=True)
    assert a1 is not a2


def test_persona_loader_birth_overrides_model_tools_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N39：birth 支持按实例覆盖模型/工具子集/系统提示词（技能即记忆的实例配置）。"""
    from unittest.mock import Mock

    import chuan.persona_loader as pl

    captured: dict[str, Any] = {}

    def fake_create(model: Any, tools: list, prompt: str,
                    name: str | None = None, checkpointer: Any = None) -> Mock:
        captured["model"] = model
        captured["tools"] = tools
        captured["prompt"] = prompt
        captured["name"] = name
        captured["checkpointer"] = checkpointer
        return Mock(name="graph")

    monkeypatch.setattr(pl, "create_react_agent", fake_create)
    loader = PersonaLoader(personas_dir=_make_persona_yaml(tmp_path, "test_agent"))
    # 覆盖模型 → 跳过 brain 解析（brains 可全空）
    loader.brains = Mock(get=lambda n: None, default=lambda: None, list=lambda: [])
    cp = object()
    loader.birth(
        "test_agent",
        model="custom-model",
        tools=["tool_a", "tool_b"],
        system_prompt="实例级私有记忆",
        checkpointer=cp,
    )
    assert captured["model"] == "custom-model"
    assert captured["tools"] == ["tool_a", "tool_b"]  # 精确替换，不加 sub_agent 工具
    assert captured["prompt"] == "实例级私有记忆"
    assert captured["checkpointer"] is cp


def test_persona_loader_birth_raises_key_error(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    with pytest.raises(KeyError):
        loader.birth("nonexistent")


def test_persona_loader_birth_all_excludes_chief(tmp_path: Path) -> None:
    _make_persona_yaml(tmp_path, "chief_of_staff")
    _make_persona_yaml(tmp_path, "lawyer")
    _make_persona_yaml(tmp_path, "programmer")
    dir_path = tmp_path / "personas"
    loader = PersonaLoader(personas_dir=dir_path)
    workers = loader.birth_all(exclude=["chief_of_staff"])
    assert "lawyer" in workers
    assert "programmer" in workers
    assert "chief_of_staff" not in workers


def test_persona_loader_kill_removes_cache(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    loader.birth("test_agent")
    assert "test_agent" in loader.list_born()
    loader.kill("test_agent")
    assert "test_agent" not in loader.list_born()


def test_persona_loader_role_map(tmp_path: Path) -> None:
    _make_persona_yaml(tmp_path, "lawyer", role="lawyer")
    _make_persona_yaml(tmp_path, "programmer")
    dir_path = tmp_path / "personas"
    loader = PersonaLoader(personas_dir=dir_path)
    mapping = loader.role_map()
    assert mapping.get("lawyer") == "lawyer"
    # persona without explicit role uses name as role
    assert mapping.get("programmer") == "programmer"


def test_persona_loader_get_persona(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    p = loader.get_persona("test_agent")
    assert p is not None
    assert p.name == "test_agent"


def test_persona_loader_get_persona_returns_none(tmp_path: Path) -> None:
    dir_path = _make_persona_yaml(tmp_path, "test_agent")
    loader = PersonaLoader(personas_dir=dir_path)
    assert loader.get_persona("nonexistent") is None


def _make_empty_config(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({"external_agents": {"enabled": [], "path": "agents"}}),
        encoding="utf-8",
    )
    return cfg


def test_persona_loader_empty_directory(tmp_path: Path) -> None:
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    cfg = _make_empty_config(tmp_path)
    loader = PersonaLoader(personas_dir=dir_path, external_config_path=cfg)
    assert loader.list_personas() == []


def test_persona_loader_nonexistent_directory(tmp_path: Path) -> None:
    cfg = _make_empty_config(tmp_path)
    loader = PersonaLoader(personas_dir=tmp_path / "nonexistent", external_config_path=cfg)
    assert loader.list_personas() == []