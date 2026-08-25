"""N4 轻量路由器测试 —— 显式锁定与关键词计分。"""

from __future__ import annotations

from pathlib import Path

import yaml

from chuan.orchestrator import Orchestrator
from chuan.persona_loader import PersonaLoader


def _make_personas(tmp_path: Path) -> PersonaLoader:
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    chief = {
        "name": "chief_of_staff",
        "display_name": "幕僚长",
        "description": "总调度",
        "brain": "cloud_general",
        "routing": {
            "explicit_lock": {
                "trigger": ["幕僚长", "切到", "锁定"],
            },
            "keyword_scoring": {
                "lawyer": ["合同", "法律", "诉讼", "律师"],
                "programmer": ["代码", "编程", "bug", "跑一下"],
                "companion": ["陪聊", "聊聊", "无聊"],
            },
            "fallback": "self",
        },
    }
    (dir_path / "chief_of_staff.yaml").write_text(
        yaml.safe_dump(chief), encoding="utf-8"
    )
    for name in ("lawyer", "programmer", "companion"):
        (dir_path / f"{name}.yaml").write_text(
            yaml.safe_dump({
                "name": name,
                "display_name": name,
                "description": f"desc for {name}",
                "brain": "cloud_general",
            }),
            encoding="utf-8",
        )
    return PersonaLoader(personas_dir=dir_path)


def test_orchestrator_routes_by_keyword(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    assert router.route("帮我看份合同") == "lawyer"


def test_orchestrator_routes_programmer_keyword(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    assert router.route("修复这个bug") == "programmer"


def test_orchestrator_routes_explicit_lock(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    assert router.route("切到 programmer") == "programmer"


def test_orchestrator_returns_none_for_unmatched(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    assert router.route("今天天气不错") is None


def test_orchestrator_returns_none_for_empty_message(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    assert router.route("") is None


def test_orchestrator_list_available_targets(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    targets = router.list_available_targets()
    assert "lawyer" in targets
    assert "programmer" in targets
    assert "chief_of_staff" not in targets


def test_orchestrator_get_routing_config(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    config = router.get_routing_config()
    assert "explicit_lock" in config
    assert "keyword_scoring" in config
    assert "fallback" in config


def test_orchestrator_keyword_scoring_returns_highest_score(tmp_path: Path) -> None:
    loader = _make_personas(tmp_path)
    router = Orchestrator(loader)
    result = router.route("写代码修bug")
    assert result == "programmer"


def test_orchestrator_without_chief_routing(tmp_path: Path) -> None:
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    (dir_path / "lawyer.yaml").write_text(
        yaml.safe_dump({
            "name": "lawyer",
            "display_name": "律师",
            "description": "法律顾问",
            "brain": "cloud_general",
        }),
        encoding="utf-8",
    )
    loader = PersonaLoader(personas_dir=dir_path)
    router = Orchestrator(loader)
    assert router.route("合同") is None
    assert router.get_routing_config() == {}


def test_orchestrator_no_keyword_scoring(tmp_path: Path) -> None:
    dir_path = tmp_path / "personas"
    dir_path.mkdir()
    (dir_path / "chief_of_staff.yaml").write_text(
        yaml.safe_dump({
            "name": "chief_of_staff",
            "display_name": "幕僚长",
            "description": "总调度",
            "brain": "cloud_general",
            "routing": {"fallback": "self"},
        }),
        encoding="utf-8",
    )
    loader = PersonaLoader(personas_dir=dir_path)
    router = Orchestrator(loader)
    assert router.route("合同") is None