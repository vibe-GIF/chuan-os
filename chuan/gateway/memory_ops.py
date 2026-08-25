"""⑤ Memory Operations —— 三层记忆统一读写接口。

职责：长期记忆 FTS 索引重建（mtime 增量同步）+ 会话巩固（蒸馏旧会话为持久笔记）。
从 RuntimeSupervisor 迁移而来（ADR-012 Gateway 拆分）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor


class MemoryOperations:
    """长期记忆索引与后台巩固调度。"""

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    def reindex(self) -> None:
        """重建长期记忆 FTS 索引（N13）+ 外接库只读索引；失败不阻断启动。"""
        memory = self._sup.memory
        if not hasattr(memory, "reindex"):
            return
        try:
            memory.reindex()
        except Exception:  # noqa: BLE001 - 索引失败不影响核心功能
            pass
        # 外接只读库（Obsidian）进 FTS：独立 vault key，启动时增量同步
        if hasattr(memory, "reindex_external"):
            try:
                report = memory.reindex_external()
                if report:
                    detail = " · ".join(f"{name} {n} 篇" for name, n in report.items())
                    print(f"[INFO] 外接库索引：{detail}")
            except Exception:  # noqa: BLE001 - 外部库失败不影响核心功能
                pass

    def kickoff_consolidation(self) -> None:
        """在常驻事件循环后台蒸馏旧会话为持久笔记（失败只告警）。

        N24：蒸馏产物经 ``chuan.wiki.Wiki.import_source`` 落到 raw 不可变层
        ``sources/``，而非旧的 ``notes/session-*.md``。
        """
        from chuan.consolidation import consolidate_sessions
        from chuan.wiki import Wiki

        sup = self._sup

        async def _run() -> None:
            try:
                wiki = Wiki(sup.memory)
                report = await consolidate_sessions(
                    sup.memory,
                    brain=sup.brains.default(),
                    max_sessions=5,
                    wiki=wiki,
                )
                if report:
                    sup.consolidation_status = f"巩固 {len(report)} 会话"
                    print(f"[INFO] 会话巩固：蒸馏 {len(report)} 个会话为 wiki 原料")
                else:
                    sup.consolidation_status = "巩固 无新内容"
            except Exception as exc:  # noqa: BLE001 - 巩固失败不影响核心功能
                sup.consolidation_status = f"巩固失败: {exc}"
                print(f"[WARNING] 会话巩固失败: {exc}")

        try:
            asyncio.run_coroutine_threadsafe(_run(), sup._loop)
        except Exception:  # noqa: BLE001 - 事件循环不可用时跳过巩固
            pass

    def run_wiki_maintenance(self) -> None:
        """N24：wiki 初始化 + 归位 + 健康检查，更新 ``sup.wiki_status``。

        - 建 5 类目录 + index/log
        - 归位（N24c）：把 sources/ 未整理的原料整理进实体页（LLM 可用则智能路由）
        - lint：报告孤立页/死链/缺元数据/过时声明
        确定性兜底，LLM 仅用于归位路由且失败自动回退，模型不可用也不阻断。
        """
        from chuan.wiki import Wiki

        sup = self._sup
        try:
            wiki = Wiki(sup.memory)
            wiki.ensure_dirs()
            try:
                ingest = wiki.ingest_sources(
                    brain=sup.brains.default(), use_llm=True, limit=5
                )
            except Exception:  # noqa: BLE001 - 归位失败不影响健康检查
                ingest = {"ingested": [], "skipped": [], "llm_routed": 0}
            report = wiki.lint()
            issues = (
                len(report["orphans"])
                + len(report["dead_links"])
                + len(report["missing_meta"])
                + len(report["stale"])
            )
            ingested = len(ingest.get("ingested", []))
            detail = f"wiki {report['pages']} 页"
            if ingested:
                detail += f" · 归位 {ingested} 原料"
            sup.wiki_status = (
                f"{detail} · 健康" if issues == 0 else f"{detail} · {issues} 处需修"
            )
        except Exception as exc:  # noqa: BLE001 - 维护失败不影响核心功能
            sup.wiki_status = f"wiki 维护失败: {exc}"

    def kickoff_wiki_maintenance(self, *, interval_hours: float = 24.0) -> None:
        """启动时立即跑一次 wiki 维护，并注册后台每日维护循环（daemon 线程）。"""
        self.run_wiki_maintenance()
        sup = self._sup
        import threading

        def _loop() -> None:
            import time

            while True:
                time.sleep(interval_hours * 3600)
                self.run_wiki_maintenance()

        try:
            threading.Thread(
                target=_loop, name="wiki-maintenance", daemon=True
            ).start()
        except Exception:  # noqa: BLE001
            pass