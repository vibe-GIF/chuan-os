"""SubAgentRegistry 测试 —— 注册、调用、安全闸集成。"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from chuan.adapters.sub_agent_registry import SubAgentRegistry, SubAgentSpec
from chuan.guard import Guard
from chuan.persona_loader import Persona, PersonaLoader


def test_register_and_get() -> None:
    registry = SubAgentRegistry()
    spec = SubAgentSpec(id="test_agent", name="Test", type="prompt", invoke={"prompt": "你是一个助手"})
    registry.register(spec)
    assert registry.get("test_agent") is spec
    assert registry.get("nonexistent") is None


def test_list_and_list_ids() -> None:
    registry = SubAgentRegistry()
    registry.register(SubAgentSpec(id="a", name="A", type="prompt"))
    registry.register(SubAgentSpec(id="b", name="B", type="prompt"))
    assert set(registry.list_ids()) == {"a", "b"}
    assert len(registry.list()) == 2


def test_remove() -> None:
    registry = SubAgentRegistry()
    registry.register(SubAgentSpec(id="x", name="X", type="prompt"))
    assert registry.remove("x") is True
    assert registry.remove("x") is False
    assert registry.get("x") is None


def test_invoke_unknown_agent() -> None:
    registry = SubAgentRegistry()
    result = registry.invoke("nonexistent", "do something")
    assert "unknown" in result


def test_invoke_prompt_type() -> None:
    registry = SubAgentRegistry()
    registry.register(SubAgentSpec(
        id="reviewer", name="Reviewer", type="prompt",
        invoke={"prompt": "请审查以下代码"},
    ))
    result = registry.invoke("reviewer", "def foo(): pass")
    assert "请审查以下代码" in result
    assert "def foo(): pass" in result


def test_invoke_command_type(tmp_path: Path) -> None:
    registry = SubAgentRegistry()
    script = tmp_path / "echo_agent.py"
    script.write_text("import sys; print(sys.stdin.read().strip())", encoding="utf-8")
    registry.register(SubAgentSpec(
        id="echoer", name="Echoer", type="command",
        invoke={"command": [sys.executable, str(script)]},
        timeout=10,
    ))
    result = registry.invoke("echoer", "hello world")
    assert result == "hello world"


def test_guard_blocks_dangerous_command() -> None:
    guard = Guard()
    registry = SubAgentRegistry(guard=guard)
    registry.register(SubAgentSpec(
        id="danger", name="Danger", type="command",
        invoke={"command": ["bash", "-c", "echo safe"]},
        timeout=10,
    ))
    result = registry.invoke("danger", "rm -rf /")
    assert "GUARD BLOCKED" in result


def test_invoke_command_timeout(tmp_path: Path) -> None:
    registry = SubAgentRegistry()
    script = tmp_path / "sleepy.py"
    script.write_text("import time; time.sleep(10)", encoding="utf-8")
    registry.register(SubAgentSpec(
        id="sleepy", name="Sleepy", type="command",
        invoke={"command": [sys.executable, str(script)]},
        timeout=1,
    ))
    result = registry.invoke("sleepy", "go")
    assert "TIMEOUT" in result


# ------------------------------------------------------------------ #
# Persona + sub_agents 集成测试
# ------------------------------------------------------------------ #
def test_persona_parses_sub_agents() -> None:
    p = Persona("programmer", {
        "display_name": "编程",
        "description": "写码",
        "sub_agents": ["prime_agent", "pi"],
    })
    assert p.sub_agents == ["prime_agent", "pi"]


def test_persona_sub_agents_defaults_to_empty() -> None:
    p = Persona("lawyer", {"display_name": "律师", "description": "法律"})
    assert p.sub_agents == []


def test_persona_system_prompt_includes_sub_agents() -> None:
    p = Persona("programmer", {
        "display_name": "编程",
        "description": "写码",
        "sub_agents": ["prime_agent"],
    })
    prompt = p.build_system_prompt()
    assert "prime_agent" in prompt
    assert "子 agent" in prompt


def test_persona_loader_resolves_sub_agent_tools(tmp_path: Path) -> None:
    registry = SubAgentRegistry()
    registry.register(SubAgentSpec(
        id="prime_agent", name="Prime Agent", type="prompt",
        invoke={"prompt": "你是一个高级编程助手"},
    ))
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    (dir_path / "programmer.yaml").write_text(
        yaml.safe_dump({
            "name": "programmer",
            "display_name": "编程",
            "description": "写码",
            "brain": "cloud_general",
            "sub_agents": ["prime_agent"],
        }),
        encoding="utf-8",
    )
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(yaml.safe_dump({"external_agents": {"enabled": [], "path": "agents"}}), encoding="utf-8")

    loader = PersonaLoader(personas_dir=dir_path, external_config_path=cfg, sub_agent_registry=registry)
    agent = loader.birth("programmer")
    assert agent.name == "programmer"


def test_persona_loader_sub_agent_tool_in_tools(tmp_path: Path) -> None:
    """验证 sub-agent 正确转为 LangChain Tool 挂载到 agent。"""
    registry = SubAgentRegistry()
    registry.register(SubAgentSpec(
        id="prime_agent", name="Prime Agent", type="prompt",
        invoke={"prompt": "你是一个高级编程助手"},
    ))
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    (dir_path / "programmer.yaml").write_text(
        yaml.safe_dump({
            "name": "programmer",
            "display_name": "编程",
            "description": "写码",
            "brain": "cloud_general",
            "sub_agents": ["prime_agent"],
        }),
        encoding="utf-8",
    )
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(yaml.safe_dump({"external_agents": {"enabled": [], "path": "agents"}}), encoding="utf-8")

    loader = PersonaLoader(personas_dir=dir_path, external_config_path=cfg, sub_agent_registry=registry)
    persona = loader.get_persona("programmer")
    assert persona is not None
    tools = loader._resolve_sub_agent_tools(persona)
    assert len(tools) == 1
    assert tools[0].name == "call_prime_agent"
    assert "Prime Agent" in tools[0].description