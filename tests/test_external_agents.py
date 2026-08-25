import sys
from pathlib import Path

from chuan.external_agents import ExternalAgentLoader
from chuan.guard import Guard


def _write_external_config(tmp_path: Path, *, enabled: str = "[echoer]") -> Path:
    config = tmp_path / "config" / "config.yaml"
    package = tmp_path / "agents" / "echoer"
    package.mkdir(parents=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"external_agents:\n  path: agents\n  enabled: {enabled}\n", encoding="utf-8")
    (package / "agent.yaml").write_text(
        "\n".join(
            [
                "name: echoer",
                "display_name: Echoer",
                "description: Echoes a task through stdin/stdout",
                "brain: local",
                "external: true",
                "command:",
                f"  - {sys.executable}",
                "  - -c",
                "  - import sys; print(sys.stdin.read())",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_only_explicitly_enabled_external_agent_is_loaded(tmp_path: Path) -> None:
    config = _write_external_config(tmp_path)
    ignored = tmp_path / "agents" / "ignored"
    ignored.mkdir()
    (ignored / "agent.yaml").write_text("name: ignored", encoding="utf-8")

    loader = ExternalAgentLoader(Guard(), config_path=config)

    assert loader.list_enabled() == ["echoer"]
    assert loader.persona_definitions()["echoer"]["external"] is True


def test_external_command_uses_stdio_and_guard(tmp_path: Path) -> None:
    loader = ExternalAgentLoader(Guard(), config_path=_write_external_config(tmp_path))
    tool = loader.tools_for("echoer")[0]

    assert tool.invoke("hello external agent") == "hello external agent"
    assert "GUARD BLOCKED" in tool.invoke("rm -rf /")


def test_external_command_builds_a_supervisor_worker(tmp_path: Path) -> None:
    loader = ExternalAgentLoader(Guard(), config_path=_write_external_config(tmp_path))
    worker = loader.build_worker("echoer")

    result = worker.invoke({"messages": [{"role": "user", "content": "delegate this"}]})
    assert result["messages"][-1].content == "delegate this"
    assert worker.name == "echoer"
