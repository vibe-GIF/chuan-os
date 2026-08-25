"""vault MCP server 测试 —— 外来 agent 经 MCP 检索/写入共享黑板（data/teams/*.json）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# 直接按路径加载 vault_server 模块（自包含，不依赖 chuan 包）
_SPEC = importlib.util.spec_from_file_location(
    "vault_server",
    Path(__file__).resolve().parent.parent / "mcp_servers" / "vault_server.py",
)
assert _SPEC is not None and _SPEC.loader is not None
vs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vs)


@pytest.fixture
def teams_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把黑板目录指向临时目录，隔离项目真实 data/teams/。"""
    d = tmp_path / "teams"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(vs, "TEAMS_DIR", d)
    monkeypatch.setattr(vs, "_ALLOWED_ROOTS", (d,))
    return d


def _write_team(d: Path, name: str, doc: dict) -> Path:
    p = d / f"{name}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# --------------------------------------------------------------------- #
# list_vaults：列出可用黑板/团队
# --------------------------------------------------------------------- #
def test_list_vaults_empty_hint(teams_dir: Path) -> None:
    out = vs.list_vaults()
    assert out == ["（暂无黑板条目，可先用 write_vault 写入）"]


def test_list_vaults_lists_teams(teams_dir: Path) -> None:
    _write_team(
        teams_dir, "A",
        {"role": "调研员", "status": "finished", "subtasks": [{"id": "s1"}]},
    )
    _write_team(
        teams_dir, "default",
        {"role": "外来 agent", "status": "running", "notes": []},
    )
    joined = "\n".join(vs.list_vaults())
    assert "A" in joined and "default" in joined
    assert "entries=1" in joined


# --------------------------------------------------------------------- #
# write_vault：写入黑板（追加/新建）
# --------------------------------------------------------------------- #
def test_write_vault_creates_new(teams_dir: Path) -> None:
    msg = vs.write_vault("部署结论", "生产环境已升级到 v2.0。", team="research")
    assert "写入成功" in msg and "research" in msg
    p = teams_dir / "research.json"
    assert p.is_file()
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["notes"][0]["key"] == "部署结论"
    assert "v2.0" in doc["notes"][0]["content"]
    assert doc["notes"][0]["written_at"]


def test_write_vault_appends(teams_dir: Path) -> None:
    vs.write_vault("todo-1", "第一条", team="default")
    vs.write_vault("todo-2", "第二条", team="default")
    doc = json.loads((teams_dir / "default.json").read_text(encoding="utf-8"))
    assert len(doc["notes"]) == 2
    assert doc["notes"][1]["key"] == "todo-2"


def test_write_vault_empty_key_or_content(teams_dir: Path) -> None:
    assert "不能为空" in vs.write_vault("", "内容")
    assert "不能为空" in vs.write_vault("键", "  ")


# --------------------------------------------------------------------- #
# search_vault：检索共享黑板
# --------------------------------------------------------------------- #
def test_search_vault_hits_task_and_subtask(teams_dir: Path) -> None:
    _write_team(
        teams_dir, "A",
        {
            "role": "调研员",
            "task": "先查A的资料，再查B的资料",
            "subtasks": [
                {"id": "s1", "description": "查A", "status": "done",
                 "summary": "服务器扩容方案已确认。"},
            ],
        },
    )
    out = vs.search_vault("服务器")
    assert "[A]" in out and "服务器" in out


def test_search_vault_hits_note(teams_dir: Path) -> None:
    vs.write_vault("部署", "生产环境已升级到 v2.0。", team="ops")
    out = vs.search_vault("v2.0", vault="ops")
    assert "[ops]" in out and "v2.0" in out


def test_search_vault_no_hit(teams_dir: Path) -> None:
    _write_team(teams_dir, "A", {"task": "查A的资料"})
    out = vs.search_vault("qwertyuiopasdfgh")
    assert "没有在黑板上找到" in out


def test_search_vault_specific_vault_isolates(teams_dir: Path) -> None:
    vs.write_vault("键", "只有 A 有这个词 AAA_唯一", team="A")
    vs.write_vault("键", "B 的无关内容", team="B")
    out = vs.search_vault("AAA_唯一", vault="A")
    assert "AAA_唯一" in out and "[B]" not in out


def test_search_vault_empty_query(teams_dir: Path) -> None:
    assert "不能为空" in vs.search_vault("   ")


# --------------------------------------------------------------------- #
# 路径安全：写操作限定在 data/teams/，防路径穿越
# --------------------------------------------------------------------- #
def test_write_vault_path_traversal_sanitized(teams_dir: Path, tmp_path: Path) -> None:
    msg = vs.write_vault("键", "内容", team="../../evil")
    # 团队名被白名单清洗，绝不写穿出 teams 目录
    assert "写入成功" in msg
    assert not (tmp_path / "evil.json").exists()
    # 清洗后的安全名落在 teams 目录内
    safe_name = vs._safe_team_name("../../evil")
    assert (teams_dir / f"{safe_name}.json").is_file()


def test_safe_team_name_strips_separators() -> None:
    assert "/" not in vs._safe_team_name(r"a/b\c")
    assert "." not in vs._safe_team_name("../x")
    assert vs._safe_team_name("   ") == "default"


# --------------------------------------------------------------------- #
# 工具暴露
# --------------------------------------------------------------------- #
def test_registered_tools() -> None:
    names = sorted(vs.mcp._tool_manager._tools.keys())
    assert names == ["list_vaults", "search_vault", "write_vault"]