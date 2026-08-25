"""N51 工具市场（chuan/tool_market.py）测试。

不依赖真实 SkillRegistry/MCP：用最小 FakeRegistry 注入
（skills.get_tools + mcp + _extra_tools），skill_registry 传 None
（_score 只按工具描述匹配，绕开 Skill 结构）。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import Tool

from chuan.tool_market import ToolMarket, load_tool_market_cfg, _tokenize

_ROOT = Path(__file__).resolve().parent.parent


class FakeSkills:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    def get_tools(self, deny=None) -> list[Tool]:
        return self._tools


class FakeRegistry:
    """ToolRegistry 最小替身：handler skills + MCP(空) + extra。"""

    def __init__(self, skill_tools: list[Tool], extra_tools: list[Tool] | None = None) -> None:
        self.skills = FakeSkills(skill_tools)
        self.mcp = None
        self._extra_tools = list(extra_tools or [])


def _tool(name: str, desc: str) -> Tool:
    return Tool(name=name, description=desc, func=lambda: "ok")


# ---------------------------------------------------------------------- #
# 目录/来源/关闭态
# ---------------------------------------------------------------------- #
def test_catalog_lists_all_with_source_and_enabled():
    reg = FakeRegistry(
        [_tool("web_search", "搜索网络获取实时信息")],
        [_tool("bash", "在 shell 执行命令")],
    )
    market = ToolMarket(reg, enabled=False)  # 默认关闭
    cat = market.catalog()
    names = {c["name"] for c in cat}
    assert names == {"web_search", "bash"}
    by_name = {c["name"]: c for c in cat}
    assert by_name["web_search"]["source"] == "skill"
    assert by_name["bash"]["source"] == "extra"
    assert all(c["enabled"] for c in cat)  # 关闭态全量挂载


def test_disabled_market_returns_all_tools():
    reg = FakeRegistry([_tool("a", "工具 A"), _tool("b", "工具 B")])
    market = ToolMarket(reg, enabled=False)
    assert market.is_enabled("a") and market.is_enabled("b")
    assert {t.name for t in market.enabled_tools()} == {"a", "b"}


# ---------------------------------------------------------------------- #
# 运行时开关（上架/下架）
# ---------------------------------------------------------------------- #
def test_enable_disable():
    reg = FakeRegistry([_tool("web_search", "搜索网络获取实时信息")], [_tool("bash", "shell")])
    market = ToolMarket(reg, enabled=True)

    assert market.disable("web_search") is True
    assert {t.name for t in market.enabled_tools()} == {"bash"}
    assert market.is_enabled("web_search") is False

    assert market.enable("web_search") is True
    assert {t.name for t in market.enabled_tools()} == {"web_search", "bash"}

    # 未知工具返回 False
    assert market.disable("nope") is False
    assert market.enable("nope") is False


# ---------------------------------------------------------------------- #
# 按信号裁剪（确定性）
# ---------------------------------------------------------------------- #
def test_select_scores_by_description_tokens():
    reg = FakeRegistry(
        [_tool("web_search", "搜索网络获取实时信息"), _tool("bash", "执行 shell 命令")]
    )
    market = ToolMarket(reg, enabled=True, min_tools=1)
    picked = market.select("帮我搜索北京的天气", min_tools=1)
    names = [t.name for t in picked]
    assert "web_search" in names
    assert "bash" not in names  # 描述无交集词元，不入选


def test_select_falls_back_to_full_when_hits_below_min():
    reg = FakeRegistry([_tool("web_search", "搜索网络"), _tool("bash", "shell")])
    market = ToolMarket(reg, enabled=True, min_tools=6)
    picked = market.select("完全无关的任务文本", min_tools=6)
    assert {t.name for t in picked} == {"web_search", "bash"}  # 不足防饿死回退全量


def test_select_respects_disabled_and_always():
    reg = FakeRegistry(
        [_tool("web_search", "搜索网络获取实时信息"), _tool("bash", "执行 shell 命令")]
    )
    market = ToolMarket(reg, enabled=True, min_tools=1, always=["bash"])
    market.disable("web_search")

    picked = market.select("搜索北京天气", min_tools=1)
    names = [t.name for t in picked]
    # web_search 已下架 → 不入选；always 名单强制保留 bash
    assert "web_search" not in names
    assert "bash" in names


# ---------------------------------------------------------------------- #
# 统计/配置读取
# ---------------------------------------------------------------------- #
def test_stats_shape():
    reg = FakeRegistry([_tool("a", "工具 A"), _tool("b", "工具 B")])
    market = ToolMarket(reg, enabled=True, min_tools=3, always=["b"])
    market.disable("a")
    st = market.stats()
    assert st["enabled"] is True
    assert st["total"] == 2
    assert st["active"] == 1
    assert st["disabled"] == ["a"]
    assert st["min_tools"] == 3
    assert st["always"] == ["b"]


def test_load_tool_market_cfg_default_off():
    cfg = load_tool_market_cfg(_ROOT / "config" / "config.yaml")
    assert cfg["enabled"] is False  # 默认关闭（ADR-009 行为不变）
    assert cfg["min_tools"] >= 1
    assert isinstance(cfg["always"], list)


def test_tokenize_zh_and_en():
    # CJK 连续串逐字拆（让中文子串可命中），英文/数字整词保留
    assert _tokenize("搜索北京天气 Weather 123") == {
        "搜", "索", "北", "京", "天", "气", "weather", "123",
    }
