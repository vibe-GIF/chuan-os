"""L3 从做到造 —— 可复用「怎么做」知识原子（N26，借鉴姜胡说 7 层 L3）。

7 层模型 L3 的核心：**重复做一件事 → 把过程提炼成可复用的知识原子 → 下次
同类任务自动复用**，从「每次从零做」升级为「照着已有做法做」。

N26 落地后并入 wiki（ADR-021 反例已更新）：howto 是 wiki 的第 6 类命名空间
（`vault/notes/howto/`），写入走 ``Wiki.write`` —— 白得 index.md 索引、lint
健康检查、双向链接、section 归并 + deprecated 留痕，与「结论/实体」页共用底座。

与既有记忆链路的分工：
- 原子结构：`## 触发场景`（何时复用）+ `## 怎么做`（步骤/经验坑），tools 走 tags
- 复用现有 Memory 的 FTS5 索引召回（`memory.recall(namespaces=["notes/howto"])`）
- 沉淀由 agent 显式 `howto_save`（识别到可复用过程时），避免自动脑补
- 复用在 `PersonaRole` 开工前自动注入（`suggest`，确定性、无 LLM）

设计约束（与项目一致）：纯本地、确定性可测；LLM 不进入读写/召回关键路径。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from chuan.wiki import (
    Wiki,
    _safe_slug,
    _split_frontmatter,
)

# 知识原子目录（notes/howto/）与召回命名空间（相对 vault 根，含 notes 前缀）
_DIRNAME = "howto"
NAMESPACE = f"notes/{_DIRNAME}"
# 自动复用注入的最低命中分：低于此分视为噪声，不注入。
# 原子正文含固定小节头「怎么做/触发场景」，会带来 怎/么/做 等通用 token 噪声
#（"今天天气怎么样" 仅因"怎么做"命中约 6 分）；阈值取 10 可过滤此类噪声，
# 只放行与原子名/触发词有实质重叠的任务（如"帮我部署周报"约 15 分）。
_INJECT_MIN_SCORE = 10.0

_SECTION = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _section(body: str, title: str) -> str:
    """提取正文中 ``## <title>`` 小节内容；无该小节返回空串。"""
    lines = body.splitlines()
    capture = False
    out: list[str] = []
    for line in lines:
        m = _SECTION.match(line)
        if m:
            if m.group(1).strip() == title:
                capture = True
                continue
            if capture:
                break
        elif capture:
            out.append(line)
    return "\n".join(out).strip()


class HowToStore:
    """L3 知识原子存储：保存/检索可复用的「怎么做」过程（wiki 第 6 类命名空间）。"""

    NAMESPACE = NAMESPACE

    def __init__(self, memory: Any) -> None:
        self.memory = memory
        self.notes_path = memory.notes_path
        self._wiki = Wiki(memory)

    def _dir(self) -> Path:
        return self.notes_path / _DIRNAME

    # ------------------------------------------------------------------ #
    # 写：委托 Wiki.write（同名归并 + 小节覆盖留痕 + index/log）
    # ------------------------------------------------------------------ #
    def save(
        self,
        name: str,
        trigger: str,
        process: str,
        *,
        tools: str | None = None,
        importance: int = 3,
        confidence: int = 3,
        source: str = "",
    ) -> Path:
        """写入/更新一个知识原子，返回其 Markdown 路径。

        - ``name``：原子名（唯一键，同名更新并保留 created，旧做法折叠留痕）
        - ``trigger``：触发场景关键词（何时该复用这个做法，写入 ``## 触发场景``）
        - ``process``：怎么做（步骤/经验坑，写入 ``## 怎么做``）
        - ``tools``：逗号分隔的涉及 agent/工具（写入 frontmatter tags）
        """
        tag_list = sorted(
            {"howto", *(t.strip() for t in str(tools or "").split(",") if t.strip())}
        )
        if trigger.strip():
            self._wiki.write(
                "howto", name, trigger, section="触发场景",
                importance=importance, confidence=confidence,
                tags=tag_list, source=source,
            )
        return self._wiki.write(
            "howto", name, process, section="怎么做",
            importance=importance, confidence=confidence,
            tags=tag_list, source=source,
        )

    # ------------------------------------------------------------------ #
    # 查：FTS5 按触发场景/内容召回 + 读全量
    # ------------------------------------------------------------------ #
    def find(
        self, query: str, *, limit: int = 5, min_score: float = 0.0
    ) -> list[dict[str, Any]]:
        """按关键词召回知识原子，返回
        [{name, trigger, tools, rel_path, score, content}]（按分降序）。
        """
        hits = self.memory.recall(
            query, namespaces=[self.NAMESPACE], limit=limit * 3
        )
        out: list[dict[str, Any]] = []
        for h in hits:
            if h.score < min_score:
                continue
            meta: dict[str, Any] = {}
            try:
                _, meta = _split_frontmatter(
                    (self.memory.vault_path / h.relative_path).read_text(
                        encoding="utf-8"
                    )
                )
            except OSError:
                pass
            body = h.content
            tags = meta.get("tags") or ()
            out.append(
                {
                    "name": Path(h.relative_path).stem,
                    "trigger": _section(body, "触发场景"),
                    "tools": tuple(t for t in tags if t != "howto"),
                    "rel_path": h.relative_path,
                    "score": h.score,
                    "content": body,
                }
            )
            if len(out) >= limit:
                break
        return out

    def get(self, name: str) -> str | None:
        """读取一个知识原子的完整 Markdown；不存在返回 None。"""
        path = self._dir() / f"{_safe_slug(name)}.md"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------ #
    # N27 自动沉淀：staging 待人工确认队列（vault 之外，避免污染 FTS/wiki）
    # ------------------------------------------------------------------ #
    def _staging_dir(self) -> Path:
        return self.memory.vault_path.parent / "howto_staging"

    def _staging_file(self, name: str) -> Path:
        return self._staging_dir() / f"{_safe_slug(name)}.json"

    def stage(
        self,
        name: str,
        trigger: str,
        process: str,
        *,
        tools: list[str] | None = None,
        source: str = "",
        task: str = "",
        role: str = "",
    ) -> dict[str, Any]:
        """把自动提炼的候选原子写入 staging 队列（待人工确认，不入 howto）。"""
        d = self._staging_dir()
        d.mkdir(parents=True, exist_ok=True)
        cand = {
            "name": name,
            "trigger": trigger,
            "process": process,
            "tools": sorted(tools or []),
            "source": source,
            "task": task,
            "role": role,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        self._staging_file(name).write_text(
            json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return cand

    def staging_list(self) -> list[dict[str, Any]]:
        """列出待人工确认的候选（按创建时间倒序）；损坏项跳过。"""
        d = self._staging_dir()
        if not d.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(d.glob("*.json"), reverse=True):
            try:
                cand = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(cand, dict) and cand.get("name"):
                out.append(cand)
        return out

    def staging_get(self, name: str) -> dict[str, Any] | None:
        """读取单个待确认候选；不存在返回 None。"""
        path = self._staging_file(name)
        if not path.exists():
            return None
        try:
            cand = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cand if isinstance(cand, dict) else None

    def approve(self, name: str, rename: str | None = None) -> Path | None:
        """人工确认：候选经 ``Wiki.write`` 落入 howto（白得 index/lint/双链），
        并删除队列项。``rename`` 可指定入库时的新原子名；返回落盘路径。"""
        cand = self.staging_get(name)
        if cand is None:
            return None
        final_name = (rename or cand["name"] or name).strip()
        tools = ",".join(str(t) for t in cand.get("tools") or [])
        path = self.save(
            final_name,
            cand.get("trigger", ""),
            cand.get("process", ""),
            tools=tools,
            source=cand.get("source") or "",
        )
        self._staging_file(name).unlink(missing_ok=True)
        return path

    def discard(self, name: str) -> bool:
        """人工否决：删除候选，不入库。"""
        path = self._staging_file(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------------ #
    # 复用：任务开工前自动注入（闭环关键）
    # ------------------------------------------------------------------ #
    def suggest(self, task: str, *, min_score: float = _INJECT_MIN_SCORE) -> str | None:
        """任务开始前自动复用：有强命中返回可注入的「参考做法」文本，否则 None。

        确定性实现（无 LLM）：按任务文本召回 top 原子，命中分低于
        ``min_score`` 视为噪声不注入。
        """
        hits = self.find(task, limit=1, min_score=min_score)
        if not hits:
            return None
        h = hits[0]
        return (
            f"【参考做法】检测到本任务与知识原子「{h['name']}」匹配"
            f"（触发场景：{h['trigger'] or '无'}）。"
            f"优先按下面的既有做法执行，再按需调整：\n{h['content']}"
        )
