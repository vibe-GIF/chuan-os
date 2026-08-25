"""N33 ACI 预判注入 —— 路由前并行预取相关上下文，注入岗位任务。

借鉴 BaiLongma：在路由决策确定目标岗位**之前**，就按用户消息并行预取
「长期记忆召回 + wiki 知识库命中」两类上下文；路由落定后把预取结果渲染成
注入块拼进岗位任务文本——agent 首轮直接带着相关背景开工，不再需要自己先
调 ``recall_memory`` / ``wiki_search`` 去摸上下文，减少首轮空转。

设计约束：
- **确定性**：全部走本地检索（FTS5 + index.md 索引），无 LLM、无网络
- **并行**：memory 与 wiki 两个召回源用 ``ThreadPoolExecutor`` 并行执行
- **旁路**：任何失败/异常静默吞掉，返回空 bundle，绝不阻断路由与执行
- **阈值**：记忆命中分低于 ``min_score`` 视为噪声不注入（与 howto suggest 同思路）

用法：
    prefetcher = AciPrefetcher(memory)
    bundle = prefetcher.prefetch("重构登录模块")   # {"memory": [...], "wiki": [...]}
    block = AciPrefetcher.render(bundle)            # 可注入文本（无命中为空串）
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

# 各类召回条数上限（防注入块过大撑爆上下文）
_MAX_MEMORY = 3
_MAX_WIKI = 3
# 每条摘要截断长度（字符）
_SNIPPET = 80
# 并行预取整体超时（秒）
_TIMEOUT = 10.0
# 记忆命中分下限：低于视为噪声不注入
_MIN_SCORE = 1.0
# 记忆召回重要性下限（0 不过滤）
_MIN_IMPORTANCE = 0


class AciPrefetcher:
    """预判注入器：按用户消息并行预取 memory + wiki 上下文。

    幂等、无副作用；``prefetch`` 可被反复调用，最近一次结果记入
    ``last_bundle`` 供 ``stats()`` 面板展示。
    """

    def __init__(
        self,
        memory: Any,
        *,
        max_memory: int = _MAX_MEMORY,
        max_wiki: int = _MAX_WIKI,
        snippet: int = _SNIPPET,
        min_score: float = _MIN_SCORE,
        min_importance: int = _MIN_IMPORTANCE,
    ) -> None:
        self._memory = memory
        self._max_memory = max_memory
        self._max_wiki = max_wiki
        self._snippet = snippet
        self._min_score = min_score
        self._min_importance = min_importance
        # 最近一次预取结果（供 /aci 面板展示，天然线程安全：整值替换）
        self.last_bundle: dict[str, Any] = {"memory": [], "wiki": []}
        self.last_rendered: str = ""

    # ------------------------------------------------------------------ #
    # 预取入口
    # ------------------------------------------------------------------ #
    def prefetch(self, message: str) -> dict[str, Any]:
        """按用户消息并行预取 memory + wiki 上下文，返回 bundle。

        bundle 结构：``{"memory": [{path, snippet, score}], "wiki": [{rel_path, snippet}]}``。
        无命中/失败时对应列表为空；整体绝不抛异常。
        """
        bundle: dict[str, Any] = {"memory": [], "wiki": []}
        if not message or not message.strip() or self._memory is None:
            self.last_bundle = bundle
            self.last_rendered = ""
            return bundle

        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="aci"
        ) as ex:
            mem_fut = ex.submit(self._recall_memory, message)
            wiki_fut = ex.submit(self._recall_wiki, message)
            try:
                bundle["memory"] = mem_fut.result(timeout=_TIMEOUT)
            except Exception:  # noqa: BLE001 - 预取是旁路
                pass
            try:
                bundle["wiki"] = wiki_fut.result(timeout=_TIMEOUT)
            except Exception:  # noqa: BLE001 - 预取是旁路
                pass

        self.last_bundle = bundle
        self.last_rendered = self.render(bundle)
        return bundle

    # ------------------------------------------------------------------ #
    # 各召回源（各自独立 try/except，互不影响）
    # ------------------------------------------------------------------ #
    # wiki 实体页所在目录（相对内部 vault）：memory.recall 按命名空间限定
    _WIKI_NS = (
        "notes/topics", "notes/entities", "notes/analysis",
        "notes/projects", "notes/howto",
    )
    _WIKI_PREFIXES = tuple(f"{ns}/" for ns in _WIKI_NS)

    def _recall_memory(self, message: str) -> list[dict[str, Any]]:
        """普通长期记忆召回（FTS5 全文索引 + 词频评分），按分阈值过滤噪声。

        与 wiki 源互斥：排除 wiki 实体页目录，避免同一文档重复注入。
        """
        try:
            hits = self._memory.recall(
                message,
                limit=self._max_memory,
                min_importance=self._min_importance,
            )
        except Exception:  # noqa: BLE001 - 记忆不可用不阻断
            return []
        return [
            {
                "path": h.relative_path,
                "snippet": self._first_line(h.content),
                "score": round(h.score, 2),
            }
            for h in hits
            if h.score >= self._min_score
            and not h.relative_path.startswith(self._WIKI_PREFIXES)
        ]

    def _recall_wiki(self, message: str) -> list[dict[str, Any]]:
        """wiki 知识库实体页召回（限定 wiki 命名空间，FTS token 匹配）。

        不走 ``search_index``（整句子串匹配对自然语言太脆弱），而是用
        ``memory.recall`` 限定 wiki 目录做 token 级匹配——比 index 更鲁棒，
        且与 memory 源互斥不重复。
        """
        try:
            hits = self._memory.recall(
                message,
                limit=self._max_wiki,
                namespaces=self._WIKI_NS,
            )
        except Exception:  # noqa: BLE001 - wiki 不可用不阻断
            return []
        return [
            {
                "rel_path": h.relative_path,
                "snippet": self._first_line(h.content),
            }
            for h in hits
        ]

    # ------------------------------------------------------------------ #
    # 渲染与统计
    # ------------------------------------------------------------------ #
    @classmethod
    def render(cls, bundle: dict[str, Any]) -> str:
        """把预取 bundle 渲染成可注入文本；无命中返回空串（不注入）。"""
        mem = bundle.get("memory") or []
        wiki = bundle.get("wiki") or []
        if not mem and not wiki:
            return ""
        lines: list[str] = []
        for h in mem:
            lines.append(
                f"- [记忆] {h.get('path', '')} — {h.get('snippet', '')}"
            )
        for h in wiki:
            lines.append(
                f"- [知识] {h.get('rel_path', '')} — {h.get('snippet', '')}"
            )
        return (
            "【预判上下文】以下相关背景已按请求预取，可直接据此回答"
            "（无需重复检索）：\n" + "\n".join(lines)
        )

    def stats(self) -> dict[str, Any]:
        """面板数据：最近一次预取各源命中数 + 是否注入。"""
        bundle = self.last_bundle
        return {
            "memory": len(bundle.get("memory") or []),
            "wiki": len(bundle.get("wiki") or []),
            "total": len((bundle.get("memory") or []))
            + len((bundle.get("wiki") or [])),
            "injected": bool(self.last_rendered),
        }

    def _first_line(self, text: str) -> str:
        """取首行非空内容并截断。"""
        for line in (text or "").splitlines():
            line = line.strip()
            if line and not line.startswith("---"):
                return line[: self._snippet]
        return (text or "").strip()[: self._snippet]
