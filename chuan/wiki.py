"""L4 记忆 —— Wiki 知识库维护层（N24，借鉴 Karpathy LLM Wiki / obsidian-second-brain / Aivy）。

在既有 `Memory`（notes/ + FTS5）之上加一层「结构化知识库」：

- **5 类目录**：`sources`(原料，只读) / `topics`(主题) / `entities`(实体) /
  `analysis`(分析结论) / `projects`(进行中的事)
- **实体页改写**：`wiki_write()` 按实体名做唯一键，同名页「合并更新」而非新建
  （保留 created、追加章节、覆盖同名小节并标注 deprecated，符合「编译而非检索」）。
- **index/log**：维护 `index.md`（每页一行索引，替代向量检索）与 `log.md`
  （追加式操作审计，grep 可回溯）——Karpathy 强调的「两个文件，不依赖 RAG」。
- **reconcile / lint**：矛盾调和（新声明覆盖旧声明并留痕）+ 健康检查
  （孤立页/死链/缺元数据/过时声明）。

设计约束（与项目一致）：
- 纯本地、零网络；核心逻辑确定性可测；LLM 仅在 `reconcile` 可选启用。
- `sources/` 视为 raw 不可变层，拒绝 `wiki_write` 直接写入（只由导入/蒸馏写入）。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# 6 类目录（Karpathy Raw→Wiki 分层 + Aivy projects/topics/entities/analysis/sources
# + N26 howto 从做到造「怎么做」过程原子）
WIKI_NAMESPACES: tuple[str, ...] = (
    "sources", "topics", "entities", "analysis", "projects", "howto",
)
# raw 不可变层：只读，不允许 wiki_write 归并写入
_READONLY_NAMESPACES: frozenset[str] = frozenset({"sources"})
# 可写成品目录（wiki_write 的目标）
WRITABLE_NAMESPACES: tuple[str, ...] = tuple(n for n in WIKI_NAMESPACES if n not in _READONLY_NAMESPACES)

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_DEPRECATED = re.compile(r"^\s*>\s*(?:旧|过时|deprecated|已作废)", re.IGNORECASE)
_SECTION = re.compile(r"^##\s+(.+)$", re.MULTILINE)
# 归位（ingest）幂等标记：原料已整理进实体页后在 frontmatter 打标，跳过重复归位
_INGEST_FLAG = "wiki_ingested"
_MAX_INGEST_SOURCE_CHARS = 4000  # 喂给 LLM 的原料截断上限

_INGEST_PROMPT = """把下面的会话原料「归位」成知识库实体条目（编译，而非检索）。

规则：
- 只从原料中提取可复用的结论/事实/待办，严禁编造、严禁补充原料之外的信息
- 每条输出一个 JSON 对象，整体是 JSON 数组，格式：
  [{"entity_type": "analysis|topics|entities|projects", "entity_name": "实体名",
    "content": "结论正文", "importance": 3, "confidence": 3}]
- entity_type 四选一：topics(主题)/entities(实体)/analysis(分析结论)/projects(进行中的事)
- 同一实体有多条结论时合并进同一条 content（用换行分隔）
- 无法归位任何内容时输出 []
- 只输出 JSON，不要 Markdown 代码块、不要任何其他文字

原料：
{source}"""


def _safe_slug(name: str) -> str:
    """把实体名转成安全文件名。

    保留 Unicode 字母/数字（含中文，`\\w` 在 Python3 默认 Unicode 模式）、
    点、横线、下划线；其余替换为 `_`。空结果回退 ``entity``。
    """
    slug = re.sub(r"[^\w.-]", "_", name, flags=re.UNICODE).strip("._-")
    return slug or "entity"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _split_frontmatter(content: str) -> tuple[str, dict[str, Any]]:
    """复用 memory 的 frontmatter 拆解（避免循环依赖的轻量复制）。"""
    if not content.startswith("---"):
        return content, {}
    text = content.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return content, {}
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return content, {}
    import yaml  # 延迟导入，保持模块轻量

    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        meta = None
    return parts[2], meta if isinstance(meta, dict) else {}


class Wiki:
    """基于一个 ``chuan.memory.Memory`` 实例的 Wiki 知识库维护层。"""

    def __init__(self, memory: Any) -> None:
        self.memory = memory
        self.vault_path = memory.vault_path
        self.notes_path = memory.notes_path

    # ------------------------------------------------------------------ #
    # 目录
    # ------------------------------------------------------------------ #
    def ensure_dirs(self) -> None:
        """确保 5 类目录 + index.md 存在（幂等）。"""
        for ns in WIKI_NAMESPACES:
            (self.notes_path / ns).mkdir(parents=True, exist_ok=True)
        for ns in WRITABLE_NAMESPACES:
            index = self.notes_path / ns / "index.md"
            if not index.exists():
                index.write_text(
                    f"# {ns} 索引\n\n> LLM 维护：每页一行（链接 + 一句话摘要）。\n\n"
                    "（暂无条目）\n",
                    encoding="utf-8",
                )
        log = self.notes_path / "log.md"
        if not log.exists():
            log.write_text("# 知识库操作日志\n\n> 追加式审计，grep 可回溯。\n\n", encoding="utf-8")

    def namespace_path(self, entity_type: str) -> Path:
        """返回某类目录的绝对路径。"""
        if entity_type not in WIKI_NAMESPACES:
            raise ValueError(f"unknown wiki namespace: {entity_type!r}")
        return self.notes_path / entity_type

    # ------------------------------------------------------------------ #
    # 实体写入（N24a 核心：改写而非追加）
    # ------------------------------------------------------------------ #
    def write(
        self,
        entity_type: str,
        name: str,
        content: str,
        *,
        importance: int = 3,
        confidence: int = 3,
        tags: Iterable[str] | None = None,
        source: str = "",
        section: str | None = None,
    ) -> Path:
        """写入/更新一个实体页。

        - ``entity_type`` ∈ topics/entities/analysis/projects（sources 只读）。
        - 同名页不存在 → 新建。
        - 同名页存在 → **合并更新**：新内容作为 ``## <section>`` 追加；
          若同名小节已存在则覆盖该小节并标注 deprecated 痕迹；保留 created。
        - 每次写入后刷新对应 index.md 并追加 log.md。
        """
        if entity_type in _READONLY_NAMESPACES:
            raise ValueError(f"{entity_type}/ 是 raw 不可变层，请走 sources 导入路径")
        self.ensure_dirs()
        ns_root = self.namespace_path(entity_type)
        path = ns_root / f"{_safe_slug(name)}.md"
        section_title = section or name

        if not path.exists():
            # 首写即带 `## <section>` 小节，保证后续同小节合并可命中
            body = f"# {name}\n\n## {section_title}\n\n{content.rstrip()}\n"
            return self._commit(path, entity_type, body, importance, confidence, tags, source, name)

        body = self._merge_body(path.read_text(encoding="utf-8"), section_title, content)
        return self._commit(path, entity_type, body, importance, confidence, tags, source, name)

    def import_source(self, name: str, content: str, *, source: str = "") -> Path:
        """向 raw 不可变层（sources/）导入原料；只允许追加，不允许覆盖已有源。"""
        self.ensure_dirs()
        path = self.namespace_path("sources") / f"{_safe_slug(name)}.md"
        if path.exists():
            # 追加到「后续来源」节，不改写原文（保持 raw 不可变语义）
            with path.open("a", encoding="utf-8") as f:
                f.write(f"\n## 补充来源（{_now()}）\n\n{content.rstrip()}\n")
            # 追加了新内容 → 清除归位标记，让 ingest 重新整理新增部分
            self._set_flag(path, _INGEST_FLAG, False)
            self._append_log("import.append", f"sources/{_safe_slug(name)}")
            return path
        body = f"# {name}\n\n{content.rstrip()}\n"
        return self._commit(path, "sources", body, 3, 3, ["source"], source, name)

    # ------------------------------------------------------------------ #
    # N24c：归位（ingest）—— 把 raw 原料主动整理进成品实体页
    # ------------------------------------------------------------------ #
    def ingest_sources(
        self, *, brain: Any | None = None, use_llm: bool = True, limit: int = 5
    ) -> dict[str, Any]:
        """把 ``sources/`` 里未整理的原料「归位」到成品实体页。

        - **LLM 可选**：``brain`` 非空且 ``use_llm`` 时，先让模型判定每条原料
          归到哪类实体（topics/entities/analysis/projects + 实体名 + 结论），
          成功解析则按实体归并写入（``Wiki.write`` 合并同名页）。
        - **确定性回退**：模型不可用/输出退化时，解析原料的 ``## 结论`` /
          ``## 待办/后续`` 小节，分别路由到 ``analysis/`` 与 ``projects/`` 同名页
          （保底不丢，符合「编译而非检索」）。
        - **幂等**：整理后的原料在 frontmatter 打 ``wiki_ingested: true``，
          下次跳过；``import_source`` 追加新内容会清标记，保证增量被重新归位。

        返回 ``{"ingested": [...], "skipped": [...], "llm_routed": int}``。
        """
        self.ensure_dirs()
        src_dir = self.namespace_path("sources")
        candidates = sorted(p for p in src_dir.glob("*.md") if p.name != "index.md")
        report: dict[str, Any] = {"ingested": [], "skipped": [], "llm_routed": 0}
        done = 0
        for path in candidates:
            if done >= limit:
                break
            text = path.read_text(encoding="utf-8")
            body, meta = _split_frontmatter(text)
            if meta.get(_INGEST_FLAG):
                report["skipped"].append(path.name)
                continue
            routed = False
            if use_llm and brain is not None:
                routed = self._ingest_via_llm(path, body, brain)
            if not routed:
                self._ingest_deterministic(path, body)
            self._set_flag(path, _INGEST_FLAG, True)
            report["ingested"].append(path.name)
            if routed:
                report["llm_routed"] += 1
            done += 1
        return report

    def _ingest_via_llm(self, path: Path, body: str, brain: Any) -> bool:
        """LLM 路由：把原料拆成若干实体条目写入；失败/退化返回 False。"""
        import json

        try:
            # 用 replace 而非 format：模板里的 JSON 大括号是字面量，不能被 format 吞掉
            prompt = _INGEST_PROMPT.replace(
                "{source}", body[:_MAX_INGEST_SOURCE_CHARS]
            )
            resp = brain.complete(
                prompt,
                system="你是知识库整理助手，只输出 JSON 数组，不要任何其他文字。",
                temperature=0.2,
            )
            start, end = resp.find("["), resp.rfind("]")
            if start == -1 or end <= start:
                return False
            items = json.loads(resp[start : end + 1])
            if not isinstance(items, list):
                return False
        except Exception:  # noqa: BLE001 - 免费模型输出退化/不可用都回退确定性
            return False

        wrote = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            entity_type = str(it.get("entity_type", "")).strip().lower()
            entity_name = str(it.get("entity_name", "")).strip()
            content = str(it.get("content", "")).strip()
            if entity_type not in WRITABLE_NAMESPACES or not entity_name or not content:
                continue
            try:
                importance = int(it.get("importance", 3))
                confidence = int(it.get("confidence", 3))
            except (TypeError, ValueError):
                importance = confidence = 3
            self.write(
                entity_type,
                entity_name,
                content,
                importance=importance,
                confidence=confidence,
                source=f"sources/{path.stem}",
            )
            wrote += 1
        return wrote > 0

    def _ingest_deterministic(self, path: Path, body: str) -> list[str]:
        """确定性归位：``## 结论`` → analysis/，``## 待办/后续`` → projects/。

        无 LLM 时的保底路径：按小节路由到对应成品层，并回链到 raw 源。
        """
        name = path.stem
        if name.startswith("session-"):
            name = name[len("session-") :] or name
        src_ref = f"[[sources/{path.stem}]]"
        sections = self._split_sections(body)
        written: list[str] = []

        conclusions = sections.get("结论", "").strip()
        if conclusions:
            content = conclusions + "\n\n（来源：" + src_ref + "）"
            self.write(
                "analysis", name, content,
                section="蒸馏结论", source=f"sources/{path.stem}",
            )
            written.append(f"analysis/{_safe_slug(name)}")

        todos = (sections.get("待办/后续", "") or sections.get("待办", "")).strip()
        if todos:
            content = todos + "\n\n（来源：" + src_ref + "）"
            self.write(
                "projects", name, content,
                section="待办", source=f"sources/{path.stem}",
            )
            written.append(f"projects/{_safe_slug(name)}")

        if not written:
            # 无可识别小节：整体截断保底入 analysis，保证不丢
            content = body.strip()[:_MAX_INGEST_SOURCE_CHARS] + "\n\n（来源：" + src_ref + "）"
            self.write(
                "analysis", name, content,
                section="蒸馏内容", source=f"sources/{path.stem}",
            )
            written.append(f"analysis/{_safe_slug(name)}")
        return written

    @staticmethod
    def _split_sections(body: str) -> dict[str, str]:
        """按 ``## 小节`` 切分正文，返回 {小节名: 内容}（不含标题行）。"""
        sections: dict[str, str] = {}
        current: str | None = None
        for line in body.splitlines():
            m = _SECTION.match(line)
            if m:
                current = m.group(1).strip()
                sections[current] = ""
            elif current is not None:
                sections[current] += line + "\n"
        return {k: v.strip() for k, v in sections.items()}

    def _set_flag(self, path: Path, flag: str, value: bool) -> None:
        """仅改写 frontmatter 中的标记位，不动正文（保持 raw 内容不可变）。"""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return
        body, meta = _split_frontmatter(text)
        if value:
            meta[flag] = True
        else:
            meta.pop(flag, None)
        import yaml  # 延迟导入，保持模块轻量

        front = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n"
        path.write_text(front + body, encoding="utf-8")

    # ------------------------------------------------------------------ #
    # 内部：合并 + 落盘 + 索引 + log
    # ------------------------------------------------------------------ #
    @staticmethod
    def _merge_body(old_body: str, section_title: str, new_content: str) -> str:
        """在旧正文里按小节合并新内容。

        规则：``## <section_title>`` 已存在 → 覆盖该小节（旧内容折叠为
        deprecated 引用）；不存在 → 追加为新的 ``## <section_title>`` 小节。
        覆盖时保留 created/updated 由 frontmatter 层处理。
        """
        section_title = section_title.strip()
        block = f"## {section_title}\n\n{new_content.rstrip()}\n"
        lines = old_body.splitlines()
        # 找匹配的 ## 小节边界
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip() == f"## {section_title}":
                header_idx = i
                break
        if header_idx is None:
            return old_body.rstrip() + "\n\n" + block + "\n"
        # 找小节结束（下一个 ## 或文件尾）
        end = len(lines)
        for j in range(header_idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        old_section = "\n".join(lines[header_idx + 1 : end]).strip()
        deprecated = (
            f"\n> 旧结论（deprecated，{_now()}）已被覆盖：\n> \n> "
            + "\n> ".join(old_section.splitlines())
        )
        new_block = block.rstrip() + deprecated + "\n"
        prefix = "\n".join(lines[:header_idx])
        suffix = "\n".join(lines[end:])
        return (prefix.rstrip("\n") + "\n\n" + new_block + "\n\n" + suffix).rstrip() + "\n"

    def _commit(
        self,
        path: Path,
        entity_type: str,
        body: str,
        importance: int,
        confidence: int,
        tags: Iterable[str] | None,
        source: str,
        name: str,
    ) -> Path:
        path.write_text(
            self.memory._with_frontmatter(
                path, body, importance, confidence, tags, source
            ),
            encoding="utf-8",
        )
        rel = path.relative_to(self.vault_path).as_posix()
        self.memory._index_document(rel, path.stem, body)
        self._refresh_index(entity_type)
        self._append_log("write", rel)
        return path

    def _refresh_index(self, entity_type: str) -> None:
        """重写某类目录的 index.md：每页一行（链接 + frontmatter 摘要）。"""
        ns_root = self.namespace_path(entity_type)
        lines = [f"# {entity_type} 索引", ""]
        lines.append("> LLM 维护：每页一行（[[wikilink]] + 一句话摘要）。")
        lines.append("")
        entries: list[str] = []
        for path in sorted(ns_root.rglob("*.md")):
            if path.name == "index.md":
                continue
            _, meta = _split_frontmatter(path.read_text(encoding="utf-8"))
            stem = path.stem
            link = f"[[{entity_type}/{stem}]]"
            snippet = ""
            if meta.get("source"):
                snippet = f" · {meta['source']}"
            entries.append(f"- {link}{snippet}")
        lines.extend(entries if entries else ["（暂无条目）"])
        (ns_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _append_log(self, action: str, detail: str) -> None:
        log = self.notes_path / "log.md"
        self.ensure_dirs()
        with log.open("a", encoding="utf-8") as f:
            f.write(f"## [{_now()}] {action} | {detail}\n")

    # ------------------------------------------------------------------ #
    # 检索辅助（index 优先，Karpathy：不用 RAG）
    # ------------------------------------------------------------------ #
    def search_index(self, query: str, *, limit: int = 10) -> list[dict[str, str]]:
        """在全部 index.md 里按关键词匹配页面（确定性的索引定位）。

        返回 [{entity_type, name, rel_path, snippet}]。命中由文件内
        [[wikilink]] 标题与摘要决定；无命中时由调用方回退 memory.recall()。
        """
        hits: list[dict[str, str]] = []
        for ns in WRITABLE_NAMESPACES:
            index = self.notes_path / ns / "index.md"
            if not index.exists():
                continue
            for line in index.read_text(encoding="utf-8").splitlines():
                m = _WIKILINK.search(line)
                if not m:
                    continue
                target = m.group(1).strip()
                if query.lower() in target.lower() or query.lower() in line.lower():
                    hits.append(
                        {
                            "entity_type": ns,
                            "name": Path(target).stem,
                            "rel_path": f"{ns}/{Path(target).name}.md",
                            "snippet": line.strip(),
                        }
                    )
        return hits[:limit]

    # ------------------------------------------------------------------ #
    # N24b：reconcile + lint
    # ------------------------------------------------------------------ #
    def reconcile(self, entity_type: str, name: str) -> dict[str, Any]:
        """对单个实体页做矛盾调和。

        确定性实现：扫描页内 ``> 旧结论（deprecated）`` 痕迹与新结论小节，
        返回 {path, deprecated_count, current_sections, verdict}。
        新声明写入即覆盖旧声明（write() 已处理），此方法负责审计与裁决报告，
        不依赖 LLM，保证免费模型环境下行为稳定。
        """
        if entity_type not in WIKI_NAMESPACES:
            raise ValueError(f"unknown wiki namespace: {entity_type!r}")
        path = self.namespace_path(entity_type) / f"{_safe_slug(name)}.md"
        if not path.exists():
            return {"path": str(path), "exists": False}
        text = path.read_text(encoding="utf-8")
        _, meta = _split_frontmatter(text)
        deprecated_count = sum(1 for line in text.splitlines() if _DEPRECATED.match(line))
        sections = [
            m.group(1).strip()
            for m in _SECTION.finditer(text)
            if not _DEPRECATED.match(m.group(0))
        ]
        verdict = "clean"
        if deprecated_count > 0:
            verdict = "superseded"  # 有被覆盖的旧声明，已由 write 调和
        return {
            "path": str(path),
            "exists": True,
            "deprecated_count": deprecated_count,
            "current_sections": sections,
            "verdict": verdict,
        }

    def lint(self) -> dict[str, Any]:
        """知识库健康检查（确定性，无需模型）。

        报告：
        - orphans：孤立页（无入链且无出链）
        - dead_links：[[wikilink]] 指向不存在的文件
        - missing_meta：缺 frontmatter 必填（importance/confidence）的页
        - stale：含 deprecated 痕迹的页
        """
        pages: dict[str, set[str]] = {}  # rel_path -> 出链集合
        for path in sorted(self.notes_path.rglob("*.md")):
            if path.name in ("log.md", "index.md"):
                continue
            rel = path.relative_to(self.notes_path).as_posix()
            text = path.read_text(encoding="utf-8")
            links = {m.group(1).strip() for m in _WIKILINK.finditer(text)}
            pages[rel] = links

        existing = set(pages.keys())
        inlinks: dict[str, int] = {}
        for src, links in pages.items():
            for target in links:
                t = target.replace(".md", "")
                candidates = [f"{t}.md"] + [
                    f"{ns}/{Path(t).name}.md" for ns in WIKI_NAMESPACES
                ]
                if any(c in existing for c in candidates):
                    inlinks[t] = inlinks.get(t, 0) + 1

        orphans = [
            rel
            for rel, links in pages.items()
            if not links and inlinks.get(Path(rel).stem, 0) == 0
        ]
        dead_links: list[str] = []
        for src, links in pages.items():
            for target in links:
                t = target.replace(".md", "")
                candidates = [f"{t}.md"] + [
                    f"{ns}/{Path(t).name}.md" for ns in WIKI_NAMESPACES
                ]
                if not any(c in existing for c in candidates):
                    dead_links.append(f"{src} -> [[{target}]]")
        missing_meta = []
        stale = []
        for rel in pages:
            path = self.notes_path / rel
            text = path.read_text(encoding="utf-8")
            _, meta = _split_frontmatter(text)
            if "importance" not in meta or "confidence" not in meta:
                missing_meta.append(rel)
            if any(_DEPRECATED.match(line) for line in text.splitlines()):
                stale.append(rel)
        return {
            "pages": len(pages),
            "orphans": sorted(orphans),
            "dead_links": sorted(dead_links),
            "missing_meta": sorted(missing_meta),
            "stale": sorted(stale),
        }
