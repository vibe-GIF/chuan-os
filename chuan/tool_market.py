"""N51 工具市场 —— 能力目录 + 运行时按信号裁剪工具集（P3 BaiLongma 借鉴）。

解决「工具越挂越多，agent 每次都要在全集里挑」的问题。对齐 ADR-009（默认全员挂载、
deny 减法），在其上加一个「市场化 + 按信号裁剪」旁路：

- **目录（catalog）**：把 ToolRegistry 里所有工具（handler skill / MCP / extra）列成
  带来源与描述的市场清单，可浏览；
- **运行时开关（enable/disable）**：动态「上架/下架」工具，下架后新 spawn 的 agent
  不再注入（经 AgentPool.tool_filter 生效）；
- **按信号裁剪（select）**：给定任务文本，**确定性**选出相关子集（命中 skill 触发词 /
  工具描述词元），命中不足回退全量（防饿死），不依赖 LLM（项目惯例：确定性路径不用模型）。

``tool_market.enabled: false``（默认）时行为与原来完全一致——零成本旁路。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain_core.tools import Tool

# 中/英文词元切分（确定性匹配用）：小写英文词 + 连续 CJK 串
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")
# 纯汉字串判定（用于逐字拆分，让中文子串可命中）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> set[str]:
    """把文本切成词元集合（供描述/任务匹配）。

    英文/数字按整词切；CJK 连续串逐字拆成单字词元——这样「搜索北京天气」
    与「搜索网络获取实时信息」能靠共享的「搜/索」子字命中（FTS5 同款惯例）。
    """
    toks: set[str] = set()
    for m in _TOKEN_RE.findall(str(text or "").lower()):
        if _CJK_RE.fullmatch(m):
            toks.update(m)  # 每个汉字独立成词元
        else:
            toks.add(m)
    return toks


def load_tool_market_cfg(config_path: str | Path = "config/config.yaml") -> dict[str, Any]:
    """读取 config.yaml 的 tool_market 段（enabled/min_tools/always）；缺省关闭。"""
    cfg: dict[str, Any] = {}
    p = Path(config_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    if p.exists():
        try:
            import yaml

            data = yaml.safe_load(p.open("r", encoding="utf-8")) or {}
            cfg = data.get("tool_market") or {}
        except Exception:  # noqa: BLE001 - 读不到按默认处理
            cfg = {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "min_tools": int(cfg.get("min_tools", 6)),
        "always": list(cfg.get("always", []) or []),
    }


class ToolMarket:
    """工具市场：目录 + 运行时开关 + 确定性按信号裁剪。"""

    def __init__(
        self,
        tool_registry: Any,
        skill_registry: Any = None,
        *,
        enabled: bool = False,
        min_tools: int = 6,
        always: list[str] | None = None,
    ) -> None:
        self._registry = tool_registry
        self._skills = skill_registry
        self._enabled = bool(enabled)
        self._min_tools = max(1, int(min_tools))
        self._always = set(always or [])
        self._disabled: set[str] = set()
        self._all: list[Tool] | None = None
        self._source_of: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 收集（懒加载一次；skill/MCP 增删后重建）
    # ------------------------------------------------------------------ #
    def _collect(self) -> list[Tool]:
        if self._all is None:
            tools: list[Tool] = []
            source: dict[str, str] = {}
            reg = self._registry
            # 1) handler skills
            for t in reg.skills.get_tools():
                tools.append(t)
                source[t.name] = "skill"
            # 2) MCP tools
            mcp = getattr(reg, "mcp", None)
            if mcp is not None:
                for s in mcp.connected_servers():
                    for t in mcp.get_tools(s):
                        tools.append(t)
                        source[t.name] = f"mcp:{s}"
            # 3) 额外注册的普通工具
            for t in getattr(reg, "_extra_tools", []) or []:
                tools.append(t)
                source[t.name] = "extra"
            self._all, self._source_of = tools, source
        return self._all

    def refresh(self) -> None:
        """MCP 连接/工具变化后重建目录。"""
        self._all = None
        self._source_of = {}

    # ------------------------------------------------------------------ #
    # 目录
    # ------------------------------------------------------------------ #
    def catalog(self) -> list[dict[str, Any]]:
        """市场清单：每项含 name/description/source/enabled。"""
        return [
            {
                "name": t.name,
                "description": str(getattr(t, "description", "") or "")[:120],
                "source": self._source_of.get(t.name, "?"),
                "enabled": self.is_enabled(t.name),
            }
            for t in self._collect()
        ]

    # ------------------------------------------------------------------ #
    # 运行时开关
    # ------------------------------------------------------------------ #
    def is_enabled(self, name: str) -> bool:
        if not self._enabled:
            return True  # 市场关闭 = 全量挂载（ADR-009）
        return name not in self._disabled

    def enable(self, name: str) -> bool:
        """上架：移除下架名单。未知工具返回 False。"""
        self._collect()  # 确保目录已加载（enable/disable 可被独立调用）
        if name not in self._source_of:
            return False
        self._disabled.discard(name)
        return True

    def disable(self, name: str) -> bool:
        """下架：加入下架名单。未知工具返回 False。"""
        self._collect()
        if name not in self._source_of:
            return False
        self._disabled.add(name)
        return True

    def enabled_tools(self) -> list[Tool]:
        """当前上架的工具（市场关闭时 = 全量）。"""
        if not self._enabled:
            return self._collect()
        return [t for t in self._collect() if t.name not in self._disabled]

    # ------------------------------------------------------------------ #
    # 按信号裁剪（确定性，不依赖 LLM）
    # ------------------------------------------------------------------ #
    def select(self, task: str, min_tools: int | None = None) -> list[Tool]:
        """给定任务文本，选出相关工具子集。

        规则（确定性）:
        1. 对上架工具按「任务词元 ∩ (描述+skill 触发词)」计分，>0 的按分降序保留；
        2. 命中数 < min_tools → 回退全量上架（防饿死，宁多勿漏）；
        3. always 名单始终追加。
        """
        min_n = self._min_tools if min_tools is None else max(1, int(min_tools))
        tokens = _tokenize(task)
        scored = [
            (self._score(t, tokens), t)
            for t in self.enabled_tools()
        ]
        scored = [(s, t) for s, t in scored if s > 0]
        scored.sort(key=lambda x: (-x[0], x[1].name))
        picked = [t for _, t in scored]
        if len(picked) < min_n:
            picked = self.enabled_tools()
        for t in self.enabled_tools():
            if t.name in self._always and t not in picked:
                picked.append(t)
        return picked

    def _score(self, tool: Tool, tokens: set[str]) -> int:
        """工具与任务的匹配分：描述词元 + skill 触发词 与任务词元的交集数。"""
        text = str(getattr(tool, "description", "") or "")
        if self._skills is not None:
            sk = self._skills.get(tool.name)
            if sk is not None:
                text += " " + " ".join(str(k) for k in (sk.trigger.get("keywords") or []))
        return len(tokens & _tokenize(text))

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def stats(self) -> dict[str, Any]:
        all_tools = self._collect()
        return {
            "enabled": self._enabled,
            "total": len(all_tools),
            "active": len(self.enabled_tools()),
            "disabled": sorted(self._disabled),
            "min_tools": self._min_tools,
            "always": sorted(self._always),
        }

