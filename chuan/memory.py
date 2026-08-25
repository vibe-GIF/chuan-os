"""L4 记忆 —— 本地 Markdown RAG 与共享黑板（ADR-004）。

记忆源文件始终是人类可读的 Markdown：``notes/`` 是长期记忆，
``shared/`` 是共享黑板，外来 agent 使用 ``shared/external/<name>/``。
检索采用无需网络或模型的本地词频/短语排序，后续可无缝替换为向量索引。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# N31 记忆「类型 + 硬容量」约束（借鉴 CC 4 类 + Hermes 2200 字符封顶）
# 类型：fact 事实 / preference 偏好 / process 过程（怎么做） / memory 默认
MEMORY_TYPES: tuple[str, ...] = ("memory", "fact", "preference", "process")
# 单条长期记忆正文硬上限（字符）：超出截断（Hermes 参考值 2200）
_MAX_DOC_CHARS = 2200

try:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    _ASYNC_SAVER_AVAILABLE = True
except ImportError:  # pragma: no cover - 缺依赖时降级为无持久化
    aiosqlite = None  # type: ignore[assignment]
    AsyncSqliteSaver = None  # type: ignore[assignment]
    _ASYNC_SAVER_AVAILABLE = False


@dataclass(frozen=True)
class MemoryHit:
    """一次长期记忆检索命中。"""

    path: Path
    content: str
    score: float
    importance: int = 0
    tags: tuple[str, ...] = ()
    # N31 记忆类型（CC 4 类：fact/preference/process/memory）
    type: str = "memory"

    @property
    def relative_path(self) -> str:
        return self.path.as_posix()


class Memory:
    """Obsidian-compatible 本地记忆仓库。"""

    _SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    _TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

    def __init__(
        self,
        vault_path: str | Path | None = None,
        *,
        config_path: str | Path = "config/config.yaml",
        embedding: Any = None,
    ) -> None:
        self._config_path = config_path
        self.vault_path = self._resolve_vault_path(vault_path, config_path)
        self.notes_path = self.vault_path / "notes"
        self.shared_path = self.vault_path / "shared"
        self.notes_path.mkdir(parents=True, exist_ok=True)
        self.shared_path.mkdir(parents=True, exist_ok=True)
        # 外接只读库（外接 Obsidian）：懒加载 config 的 memory.external_vaults
        self._external_vaults: list[tuple[str, Path]] | None = None
        # LangGraph 会话状态持久化到 data/sessions.db（重启不丢）
        # 长期记忆仍持久化在上述 Markdown 中。
        # 注意：运行时 agent 图走异步 ainvoke，必须用 AsyncSqliteSaver
        # （SqliteSaver 只有同步方法，异步调用会抛 NotImplementedError）。
        # aiosqlite 连接绑定事件循环，所以懒初始化：setup_async() 必须在
        # 后续 ainvoke 所在的同一个事件循环里先调用一次。
        self._db_path = self._resolve_db_path(config_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._aio_conn: Any = None
        self.checkpointer: Any = None
        # FTS5 全文索引（N13）：data/memory_fts.db，加速长期记忆召回。
        # 纯本地、零网络/模型依赖；写入时增量索引，启动时 reindex 兜底。
        self._fts_db_path = self._db_path.parent / "memory_fts.db"
        self._fts: sqlite3.Connection | None = None
        self._fts_lock = threading.Lock()
        # N43 语义检索（sqlite-vec，FTS5 不动）：嵌入源 = 显式注入 / config 云端 / 关闭。
        #   embedding=False  → 关闭语义（纯词法 FTS5，零额外依赖，默认）
        #   embedding=callable → 直接用（测试注入确定性 stub）
        #   embedding=None    → 按 config memory.semantic（enabled + key 可用才建）
        # 语义是旁路：嵌入/建表/检索任何失败都静默降级回 FTS5，绝不阻断主流程。
        self._embedding: Any = self._resolve_embedding(embedding)
        self._vec_ready: bool = False

    async def setup_async(self) -> Any:
        """在当前事件循环里创建 AsyncSqliteSaver（幂等）。

        必须在与 agent 图 ainvoke 相同的事件循环中调用，
        由 RuntimeSupervisor.wake_up() 在常驻循环线程上完成。
        缺 aiosqlite 依赖时保持 None，降级为不持久化。
        """
        if self.checkpointer is not None or not _ASYNC_SAVER_AVAILABLE:
            return self.checkpointer
        # aiosqlite 0.20+ 的 Connection 本身是 awaitable：await 它完成连接
        conn = aiosqlite.connect(str(self._db_path))
        await conn
        saver = AsyncSqliteSaver(conn)
        await saver.setup()  # 建表（已存在则跳过）
        self._aio_conn = conn
        self.checkpointer = saver
        return self.checkpointer

    async def close_async(self) -> None:
        """关闭底层 aiosqlite 连接（幂等）。"""
        conn, self._aio_conn = self._aio_conn, None
        self.checkpointer = None
        if conn is not None:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败不阻断退出
                pass

    def remember(
        self,
        name: str,
        content: str,
        *,
        namespace: str = "notes",
        importance: int = 3,
        confidence: int = 3,
        tags: Iterable[str] | None = None,
        source: str = "",
        type: str = "memory",
    ) -> Path:
        """写入或覆盖一条长期记忆，返回其 Markdown 路径。

        元数据写入 YAML frontmatter（created/updated/importance/confidence/tags/source/type），
        供 recall 按重要性门控、人类与 Obsidian 阅读；覆盖写时保留原 created。
        confidence 为置信度（1-5，默认 3）：wiki 归并/调和时按它裁决新旧声明。

        N31 约束：``type`` 为记忆类型（fact/preference/process/memory，非法值归
        memory）；正文超过 ``_MAX_DOC_CHARS``（2200）字符硬截断，防止单条失控。
        """
        type = type if type in MEMORY_TYPES else "memory"
        body = content.rstrip()[:_MAX_DOC_CHARS].rstrip() + "\n"
        path = self._document_path(namespace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self._with_frontmatter(
                path, body, importance, confidence, tags, source, type
            ),
            encoding="utf-8",
        )
        self._index_document(
            path.relative_to(self.vault_path).as_posix(), path.stem, body
        )
        return path

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        namespaces: Iterable[str] | None = None,
        min_importance: int = 0,
        vaults: Iterable[str] | None = None,
        type: str | None = None,
    ) -> list[MemoryHit]:
        """从长期记忆召回最相关的 Markdown；默认仅检索 ``notes/``。

        开启 FTS5 全文索引候选（N13）：命中词生成 OR 候选文档，
        避免每次扫全盘；候选仍按既有词频评分排序。若索引不可用或
        无结果，回退到全盘扫描，保证结果不因索引空窗而丢失。

        min_importance 为重要性门控：只返回 importance >= 该值的命中
        （无 frontmatter 的旧文档按 importance=0 处理），默认 0 不过滤。

        ``type``（可选）：记忆类型过滤（fact/preference/process/memory），
        只返回该类型命中；默认不过滤。

        ``vaults``（可选）：外部库名列表（config ``memory.external_vaults``）。
        只在显式请求时跨库召回（只读，独立 vault key），默认不混入内部管道；
        外部文档无 frontmatter 时按 importance=3 处理。
        """
        if limit <= 0 or not query.strip():
            return []

        roots = list(namespaces) if namespaces is not None else ["notes"]
        query_tokens = self._tokens(query)

        # FTS 快速候选：仅对非黑板命名空间生效
        paths_to_score: set[Path] | None = None
        scope_roots = [r for r in roots if not self._is_shared_namespace(r)]
        if scope_roots and query_tokens:
            cands = self._fts_candidate_paths(query_tokens)
            # cands == None → FTS 不可用/建索引失败 → 全盘；空集合 → 全盘兜底
            if cands is not None and cands:
                paths_to_score = cands

        hits: list[MemoryHit] = []
        for namespace in roots:
            root = self._namespace_path(namespace)
            # 黑板不会未经请求混进长期记忆上下文。
            if not root.exists() or self._is_shared_namespace(namespace):
                continue
            if paths_to_score is not None:
                candidates = (
                    path
                    for path in paths_to_score
                    if path.is_relative_to(root)
                )
            else:
                candidates = root.rglob("*.md")
            self._recall_root(
                query, query_tokens, root, candidates,
                min_importance, rel_base=self.vault_path, hits=hits,
            )

        # 外部库：只读、显式请求才召回（独立 vault key，不混入内部管道）
        # vaults=None → 不查外接库；空列表 → 检索全部已配置外接库
        if vaults is not None:
            external = {name: root for name, root in self._load_external_vaults()}
            for name in vaults or list(external):
                root = external.get(name)
                if root is None or not root.exists():
                    continue
                cands = (
                    self._fts_candidate_paths(
                        query_tokens, root=root, vault_key=self._vault_key_for(root)
                    )
                    if query_tokens
                    else None
                )
                if cands:
                    candidates = (p for p in cands)
                else:
                    candidates = (
                        p
                        for p in root.rglob("*.md")
                        if not any(
                            part.startswith(".")
                            for part in p.relative_to(root).parts
                        )
                    )
                self._recall_root(
                    query, query_tokens, root, candidates,
                    min_importance, rel_base=root, hits=hits, default_importance=3,
                )

        # N43 语义检索：词法 FTS5 + 语义向量双路合并（语义关闭/失败时 hits 不变）
        # 只对非黑板命名空间做语义召回；外部库仍走词法（V1 不混入内部管道）。
        if scope_roots:
            sem_hits = self._recall_semantic(query, scope_roots, min_importance)
            hits = self._merge_hits(hits, sem_hits)

        hits.sort(key=lambda hit: (-hit.score, hit.relative_path))
        if type is not None:
            hits = [h for h in hits if h.type == type]
        return hits[:limit]

    def _recall_root(
        self,
        query: str,
        query_tokens: list[str],
        root: Path,
        paths: Iterable[Path],
        min_importance: int,
        *,
        rel_base: Path,
        hits: list[MemoryHit],
        default_importance: int = 0,
    ) -> None:
        """对 ``root`` 下的候选路径做词频评分并追加命中到 ``hits``。

        ``rel_base``：MemoryHit.path 的相对基准（内部=内部 vault，外部=外部库）。
        ``default_importance``：无 frontmatter 时的默认重要性
        （外部库默认 3，内部保持 0 与历史一致）。
        """
        for path in paths:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            body, meta = self._split_frontmatter(content)
            imp = meta.get("importance")
            importance = int(imp) if imp is not None else default_importance
            if importance < min_importance:
                continue
            score = self._score(query, query_tokens, body, filename=path.stem)
            if score > 0:
                tags = meta.get("tags") or ()
                hits.append(
                    MemoryHit(
                        path.relative_to(rel_base),
                        body,
                        score,
                        importance=importance,
                        tags=tuple(str(t) for t in tags),
                        type=str(meta.get("type") or "memory"),
                    )
                )

    # ------------------------------------------------------------------ #
    # FTS5 全文索引（N13）
    # ------------------------------------------------------------------ #
    _CJK_PATTERN = re.compile(r"([\u4e00-\u9fff])")

    def _vault_key(self) -> str:
        return self._vault_key_for(self.vault_path)

    @classmethod
    def _vault_key_for(cls, root: Path) -> str:
        """vault 索引键：以根路径标识（内部=内部 vault，外部=外部库根）。"""
        return str(root.resolve()).lower()

    @classmethod
    def _fts_segment(cls, text: str) -> str:
        """把每个 CJK 字符拆成独立 token（unicode61 会连写中文为一个 token）。"""
        return cls._CJK_PATTERN.sub(r"\1 ", text)

    def _ensure_fts(self) -> sqlite3.Connection:
        """惰性创建 FTS5 连接；建表失败时返回未连接状态由调用方降级。"""
        lock = self._fts_lock
        with lock:
            if self._fts is not None:
                return self._fts
            self._fts_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._fts_db_path), check_same_thread=False)
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "content, vault UNINDEXED, rel_path UNINDEXED, title UNINDEXED,"
                " tokenize='unicode61')"
            )
            # 同步元数据：vault + rel_path → mtime，供增量 reindex 跳过未变更文件。
            # 放普通表而非 FTS5 列，避免老库 ALTER 的兼容问题。
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_meta ("
                "vault TEXT NOT NULL, rel_path TEXT NOT NULL,"
                "mtime INTEGER NOT NULL,"
                "PRIMARY KEY (vault, rel_path))"
            )
            conn.commit()
            self._fts = conn
        return conn

    def _index_document(
        self,
        rel_path: str,
        title: str,
        content: str,
        *,
        mtime: int | None = None,
        root: Path | None = None,
        vault_key: str | None = None,
    ) -> None:
        """按 rel_path + vault 幂等 upsert 一条文档，并记录其 mtime；失败静默降级。

        mtime 为文档最后修改时间戳，供增量 reindex 判断文件是否已变更。
        未显式传入时从磁盘 stat（remember() 写盘后调用可拿到）。
        ``root``/``vault_key``（可选）：外部库时传入其根与 vault 键，
        默认内部 vault（保持向后兼容）。
        """
        root = root or self.vault_path
        vault_key = vault_key or self._vault_key_for(root)
        try:
            conn = self._ensure_fts()
        except sqlite3.Error:  # pragma: no cover - SQLite/FTS5 不可用
            return
        # 只索引正文，frontmatter 的时间戳/标签不参与检索，避免噪声
        body, _ = self._split_frontmatter(content)
        seg = self._fts_segment(body)
        if mtime is None:
            try:
                mtime = int((root / rel_path).stat().st_mtime)
            except OSError:
                mtime = 0
        with self._fts_lock:
            cursor = conn.execute(
                "DELETE FROM memory_fts WHERE rel_path=? AND vault=?",
                (rel_path, vault_key),
            )
            cursor.executemany(  # type: ignore[union-attr]
                "INSERT INTO memory_fts (content, vault, rel_path, title) "
                "VALUES (?,?,?,?)",
                [(seg, vault_key, rel_path, title)],
            )
            conn.execute(
                "INSERT INTO memory_meta (vault, rel_path, mtime) VALUES (?,?,?) "
                "ON CONFLICT(vault, rel_path) DO UPDATE SET mtime=excluded.mtime",
                (vault_key, rel_path, mtime),
            )
            conn.commit()
        # N43 语义旁路：向量索引（FTS 提交后再写，失败静默降级，真相不动）
        self._index_semantic(rel_path, title, body, vault_key)

    def _fts_candidate_paths(
        self,
        query_tokens: list[str],
        *,
        root: Path | None = None,
        vault_key: str | None = None,
    ) -> set[Path] | None:
        """返回命中任一 token 的文档路径；FTS 不可用/出错返回 None 触发全盘。

        ``root``/``vault_key``（可选）：外部库时传入其根与 vault 键，
        默认内部 vault。
        """
        root = root or self.vault_path
        vault_key = vault_key or self._vault_key_for(root)
        try:
            conn = self._ensure_fts()
        except sqlite3.Error:  # pragma: no cover
            return None
        if not query_tokens:
            return None
        match_expr = " OR ".join(f'"{t}"' for t in query_tokens)
        rows = []
        try:
            with self._fts_lock:
                cursor = conn.execute(
                    "SELECT rel_path FROM memory_fts WHERE vault=? AND "
                    "memory_fts MATCH ?",
                    (vault_key, match_expr),
                )
                rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return None
        found: set[Path] = set()
        for (rel_path,) in rows:
            path = root / rel_path
            if path.exists():
                found.add(path)
        return found

    # ------------------------------------------------------------------ #
    # N43 语义检索（sqlite-vec，FTS5 不动）：向量索引旁路 + 双路合并
    # ------------------------------------------------------------------ #
    def _resolve_embedding(self, injected: Any) -> Any:
        """解析嵌入源：False=关闭；callable=直接用；None=按 config（enabled+key 可用才建）。

        任何配置/构建失败都返回 False（关闭语义），绝不抛错——语义是旁路。
        """
        if injected is False:
            return False
        # callable（裸函数 stub）或 EmbeddingClient 实例（有 .embed）都直接采用
        if callable(injected) or hasattr(injected, "embed"):
            return injected
        try:
            from chuan.embed import EmbeddingClient

            cfg = self._semantic_config()
            if not cfg.get("enabled"):
                return False
            client = EmbeddingClient.from_config(cfg)
            return client if client is not None else False
        except Exception:  # noqa: BLE001 - 嵌入源构建失败即关闭，不影响主流程
            return False

    def _semantic_config(self) -> dict[str, Any]:
        """从 config.yaml 读 ``memory.semantic`` 段（缺失返回空 dict）。"""
        config = Path(self._config_path)
        if not config.is_absolute():
            config = self._project_root() / config
        data: dict[str, Any] = {}
        if config.exists():
            try:
                with config.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):  # pragma: no cover
                data = {}
        return data.get("memory", {}).get("semantic", {}) or {}

    def _semantic_enabled(self) -> bool:
        """语义层是否启用（有可用嵌入源）。"""
        return self._embedding is not False and self._embedding is not None

    def _semantic_dim(self) -> int:
        """嵌入向量维度：嵌入源自带优先，否则取 config dim。"""
        dim = getattr(self._embedding, "dim", None)
        if dim:
            return int(dim)
        try:
            return int(self._semantic_config().get("dim") or 0)
        except (TypeError, ValueError):  # pragma: no cover
            return 0

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """调嵌入源；失败/不可用返回空（旁路降级）。支持 client.embed 或裸 callable。"""
        if not self._semantic_enabled() or not texts:
            return []
        try:
            if hasattr(self._embedding, "embed"):
                return self._embedding.embed(texts)
            return self._embedding(texts)
        except Exception:  # noqa: BLE001 - 嵌入失败降级为空
            return []

    @staticmethod
    def _vec_fmt(vec: list[float]) -> str:
        """把向量格式化成 sqlite-vec 的文本字面量 '[x,y,...]'。"""
        return "[" + ",".join(repr(float(x)) for x in vec) + "]"

    def _ensure_vec(self) -> sqlite3.Connection | None:
        """惰性在 FTS 连接上加载 sqlite-vec 并建 memory_vec；失败返回 None（语义降级）。

        vec 表建在共享 memory_fts.db 里，维度由首次创建锁死（IF NOT EXISTS 不重建）；
        若现有表声明维度与当前嵌入源不一致（如测试 dim=4 污染后真实 1024），
        DROP 重建——向量索引是纯派生数据，reindex 会按需回填，重建安全。
        """
        if not self._semantic_enabled():
            return None
        conn = self._ensure_fts()
        if self._vec_ready:
            return conn
        dim = self._semantic_dim()
        if dim <= 0:
            return None
        with self._fts_lock:
            try:
                conn.enable_load_extension(True)
                import sqlite_vec

                sqlite_vec.load(conn)
                row = conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='memory_vec'"
                ).fetchone()
                if row is not None and f"float[{dim}]" not in (row[0] or ""):
                    conn.execute("DROP TABLE IF EXISTS memory_vec")
                # vec0 元数据列（+vault/+rel_path/+title）可存储可查；
                # KNN 查询不能对元数据列加 WHERE（sqlite-vec 限制），跨库隔离在 Python 侧做。
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
                    f"embedding float[{dim}] distance_metric=cosine,"
                    f"+vault TEXT, +rel_path TEXT, +title TEXT)"
                )
                conn.commit()
                self._vec_ready = True
            except Exception:  # noqa: BLE001 - 扩展不可用/维度非法 → 语义降级
                self._vec_ready = False
                return None
        return conn

    def _index_semantic(
        self, rel_path: str, title: str, body: str, vault_key: str
    ) -> None:
        """把文档写入向量索引（旁路：任何失败静默降级，FTS5 真相不动）。

        与 FTS 同库同事务锁；正文截断前 2000 字符（足够表达语义，省 token）。
        """
        if not self._semantic_enabled():
            return
        dim = self._semantic_dim()
        conn = self._ensure_vec()
        if conn is None or dim <= 0:
            return
        vecs = self._embed([body[:2000]])
        if not vecs or len(vecs[0]) != dim:
            return  # 维度不匹配/失败 → 跳过该条，下次 reindex 回填
        with self._fts_lock:
            try:
                conn.execute(
                    "DELETE FROM memory_vec WHERE vault=? AND rel_path=?",
                    (vault_key, rel_path),
                )
                conn.execute(
                    "INSERT INTO memory_vec (embedding, vault, rel_path, title) "
                    "VALUES (?,?,?,?)",
                    (self._vec_fmt(vecs[0]), vault_key, rel_path, title),
                )
                conn.commit()
            except Exception:  # noqa: BLE001 - 向量写入失败降级
                pass

    def _vec_hits(
        self, query_vec: list[float], k: int
    ) -> list[tuple[str, str, float]]:
        """KNN top-K：返回 (rel_path, vault_key, distance)；空/失败返回空。"""
        conn = self._ensure_vec()
        if conn is None:
            return []
        fmt = self._vec_fmt(query_vec)
        try:
            with self._fts_lock:
                rows = conn.execute(
                    "SELECT rel_path, vault, distance FROM memory_vec "
                    "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (fmt, k),
                ).fetchall()
        except Exception:  # noqa: BLE001 - 检索失败降级
            return []
        return [(r[0], r[1], float(r[2])) for r in rows]

    def _recall_semantic(
        self,
        query: str,
        scope_roots: list[str],
        min_importance: int,
    ) -> list[MemoryHit]:
        """语义召回：嵌入查询 → KNN top-K → Python 侧按 vault/命名空间过滤 → 打分。

        只作用于非黑板命名空间（scope_roots，与 FTS 候选一致）；外部库走独立
        vault key，内部召回默认不混入（语义 V1 只服务内部记忆，外部仍词法）。
        打分 = weight * (1 - distance)，与 FTS 命中按路径合并累加（双路合并）。
        """
        if not self._semantic_enabled() or not scope_roots:
            return []
        dim = self._semantic_dim()
        vecs = self._embed([query])
        if not vecs or len(vecs[0]) != dim:
            return []
        vault_key = self._vault_key()
        weight = float(self._semantic_config().get("weight") or 1.0)
        k = max(len(scope_roots) * 10, 20)  # 跨库取多点，过滤后仍够
        hits: list[MemoryHit] = []
        for rel_path, vault, distance in self._vec_hits(vecs[0], k):
            if vault != vault_key:
                continue
            path = self.vault_path / rel_path
            if not path.is_relative_to(self.vault_path):
                continue
            if not any(
                path.is_relative_to(self._namespace_path(ns)) for ns in scope_roots
            ):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            body, meta = self._split_frontmatter(content)
            imp = meta.get("importance")
            importance = int(imp) if imp is not None else 0
            if importance < min_importance:
                continue
            score = weight * (1.0 - distance)
            if score <= 0:
                continue
            hits.append(
                MemoryHit(
                    path.relative_to(self.vault_path),
                    body,
                    score,
                    importance=importance,
                    tags=tuple(str(t) for t in (meta.get("tags") or ())),
                    type=str(meta.get("type") or "memory"),
                )
            )
        return hits

    @staticmethod
    def _merge_hits(a: list[MemoryHit], b: list[MemoryHit]) -> list[MemoryHit]:
        """双路合并：按相对路径累加分数（FTS 命中 + 语义命中都加分）。"""
        merged: dict[str, MemoryHit] = {}
        for h in [*a, *b]:
            key = h.relative_path
            old = merged.get(key)
            if old is None:
                merged[key] = h
            else:
                merged[key] = MemoryHit(
                    old.path,
                    old.content,
                    old.score + h.score,
                    importance=max(old.importance, h.importance),
                    tags=old.tags or h.tags,
                    type=old.type if old.type != "memory" else h.type,
                )
        return list(merged.values())

    def reindex(self, *, namespace: str = "notes") -> int:
        """增量同步 `namespace` 下的 Markdown 与 FTS 索引，返回本次变更文档数。

        变更检测按 mtime（存于 memory_meta）：
        - 磁盘 mtime 与索引记录一致 → 跳过（未变更，不重读文件）
        - 新文件或内容已修改 → 重建该文档索引
        - meta 有记录但磁盘已删除/改名 → 清除索引残留

        老库首次运行时 meta 为空 → 全量重建一次，行为等同旧实现。
        """
        root = self._namespace_path(namespace)
        if not root.exists():
            return 0
        return self._reindex_root(
            root,
            self._vault_key_for(self.vault_path),
            rel_base=self.vault_path,
        )

    def _reindex_root(
        self,
        root: Path,
        vault_key: str,
        rel_base: Path,
        *,
        exclude_hidden: bool = False,
    ) -> int:
        """增量同步 ``root`` 下的 Markdown 与 FTS（vault_key 隔离），返回变更数。

        ``rel_base``：rel_path 的基准（内部=内部 vault 根，外部=外部库根），
        保证 rel_path 语义与各自召回映射一致。
        ``exclude_hidden``：跳过隐藏目录（.obsidian/.trash/.git 等），
        供外部库索引避免把配置/附件扫进来。
        """
        if not root.exists():
            return 0
        try:
            conn = self._ensure_fts()
        except sqlite3.Error:  # pragma: no cover
            return 0

        paths = root.rglob("*.md")
        if exclude_hidden:
            paths = (
                p
                for p in paths
                if not any(part.startswith(".") for part in p.relative_to(root).parts)
            )
        disk_files: dict[str, int] = {}  # rel_path -> mtime
        for path in sorted(paths):
            rel_path = path.relative_to(rel_base).as_posix()
            try:
                disk_files[rel_path] = int(path.stat().st_mtime)
            except OSError:
                continue

        with self._fts_lock:
            known = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT rel_path, mtime FROM memory_meta WHERE vault=?",
                    (vault_key,),
                )
            }
            needs_index = [
                (rel_path, mtime)
                for rel_path, mtime in disk_files.items()
                if known.get(rel_path) != mtime
            ]
            stale = [rel_path for rel_path in known if rel_path not in disk_files]

        # N43 语义回填：FTS 已索引但向量缺失的文档补建向量（老库开启语义时兜底；
        # 嵌入失败/未启用时跳过，下次 reindex 再试——自愈，绝不影响词法索引）。
        if self._semantic_enabled() and self._ensure_vec() is not None:
            try:
                with self._fts_lock:
                    vec_known = {
                        row[0]
                        for row in conn.execute(
                            "SELECT rel_path FROM memory_vec WHERE vault=?",
                            (vault_key,),
                        )
                    }
                indexed = {rel for rel, _ in needs_index}
                for rel_path in known:
                    if (
                        rel_path not in vec_known
                        and rel_path in disk_files
                        and rel_path not in indexed
                    ):
                        needs_index.append((rel_path, disk_files[rel_path]))
            except Exception:  # noqa: BLE001 - 回填失败降级
                pass

        # 锁外重建索引（_index_document 内部会重新拿锁，锁不可重入）
        changed = 0
        for rel_path, mtime in needs_index:
            path = rel_base / rel_path
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            self._index_document(
                rel_path, path.stem, content, mtime=mtime,
                root=rel_base, vault_key=vault_key,
            )
            changed += 1

        if stale:
            with self._fts_lock:
                for rel_path in stale:
                    conn.execute(
                        "DELETE FROM memory_fts WHERE vault=? AND rel_path=?",
                        (vault_key, rel_path),
                    )
                    conn.execute(
                        "DELETE FROM memory_meta WHERE vault=? AND rel_path=?",
                        (vault_key, rel_path),
                    )
                    # N43：向量索引同步清理（语义启用时表已建；未启用跳过）
                    if self._semantic_enabled():
                        try:
                            conn.execute(
                                "DELETE FROM memory_vec WHERE vault=? AND rel_path=?",
                                (vault_key, rel_path),
                            )
                        except Exception:  # noqa: BLE001
                            pass
                conn.commit()
                changed += len(stale)
        return changed

    def reindex_external(self) -> dict[str, int]:
        """增量索引所有配置的外部库（只读）到 FTS，返回 {name: 变更文档数}。

        外部库走独立 vault key（``_vault_key_for(root)``），与内部记忆隔离；
        只读文件内容进索引，绝不写回。跳过隐藏目录（.obsidian/.trash/.git）。
        """
        report: dict[str, int] = {}
        for name, root in self._load_external_vaults():
            if not root.exists():
                continue
            report[name] = self._reindex_root(
                root, self._vault_key_for(root), rel_base=root, exclude_hidden=True
            )
        return report

    def _load_external_vaults(self) -> list[tuple[str, Path]]:
        """懒加载外部库列表（name, root）；未配置/目录不存在时为空。"""
        if self._external_vaults is None:
            self._external_vaults = self._resolve_external_vaults(self._config_path)
        return self._external_vaults

    @classmethod
    def _resolve_external_vaults(cls, config_path: str | Path) -> list[tuple[str, Path]]:
        """从 config 的 ``memory.external_vaults`` 解析外部库（name, 绝对路径）。"""
        config = Path(config_path)
        if not config.is_absolute():
            config = cls._project_root() / config
        out: list[tuple[str, Path]] = []
        if not config.exists():
            return out
        with config.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        for item in data.get("memory", {}).get("external_vaults", []) or []:
            name = str(item.get("name", "")).strip()
            raw = str(item.get("path", "")).strip()
            if not name or not raw:
                continue
            candidate = Path(raw)
            path = (
                candidate.resolve()
                if candidate.is_absolute()
                else (cls._project_root() / candidate).resolve()
            )
            out.append((name, path))
        return out

    # ------------------------------------------------------------------ #
    # 黑板
    # ------------------------------------------------------------------ #
    def blackboard_namespace(self, *, external_agent: str | None = None) -> str:
        """核心角色共用 ``shared/``；外来 agent 使用 ADR-006 隔离目录。"""
        if external_agent is None:
            return "shared"
        self._validate_segment(external_agent)
        return f"shared/external/{external_agent}"

    def write_blackboard(
        self, name: str, content: str, *, external_agent: str | None = None
    ) -> Path:
        """写入共享黑板；外来 agent 的写入自动隔离。"""
        path = self._document_path(
            self.blackboard_namespace(external_agent=external_agent), name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path

    def read_blackboard(
        self, name: str, *, external_agent: str | None = None
    ) -> str | None:
        """读取共享黑板；不存在时返回 ``None``。"""
        path = self._document_path(
            self.blackboard_namespace(external_agent=external_agent), name
        )
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def list_blackboard(self, *, external_agent: str | None = None) -> list[str]:
        """列出当前黑板命名空间下的条目（不含扩展名）。"""
        namespace = self.blackboard_namespace(external_agent=external_agent)
        root = self._namespace_path(namespace)
        if not root.exists():
            return []
        return sorted(
            str(path.relative_to(root).with_suffix("")) for path in root.rglob("*.md")
        )

    @classmethod
    def _project_root(cls) -> Path:
        return Path(__file__).resolve().parent.parent

    @classmethod
    def _resolve_vault_path(
        cls, vault_path: str | Path | None, config_path: str | Path
    ) -> Path:
        if vault_path is not None:
            candidate = Path(vault_path)
        else:
            config = Path(config_path)
            if not config.is_absolute():
                config = cls._project_root() / config
            configured = ""
            if config.exists():
                with config.open("r", encoding="utf-8") as file:
                    data = yaml.safe_load(file) or {}
                    configured = data.get("memory", {}).get("vault_path", "")
            candidate = (
                Path(configured)
                if configured
                else cls._project_root() / "data" / "memory" / "vault"
            )
        if candidate.is_absolute():
            return candidate.resolve()
        return (cls._project_root() / candidate).resolve()

    @classmethod
    def _resolve_db_path(cls, config_path: str | Path) -> Path:
        """解析 SqliteSaver 数据库文件路径。

        默认位置: data/sessions.db（项目根目录下）
        """
        config = Path(config_path)
        if not config.is_absolute():
            config = cls._project_root() / config
        db_path = cls._project_root() / "data" / "sessions.db"
        if config.exists():
            with config.open("r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
            configured = data.get("memory", {}).get("session_db_path", "")
            if configured:
                candidate = Path(configured)
                if candidate.is_absolute():
                    return candidate.resolve()
                return (cls._project_root() / candidate).resolve()
        return db_path.resolve()

    def _namespace_path(self, namespace: str) -> Path:
        segments = self._validate_namespace(namespace)
        path = self.vault_path.joinpath(*segments)
        if self.vault_path not in (path, *path.parents):
            raise ValueError("memory namespace escapes the vault")
        return path

    def _document_path(self, namespace: str, name: str) -> Path:
        self._validate_segment(name)
        return self._namespace_path(namespace) / f"{name}.md"

    def _validate_namespace(self, namespace: str) -> list[str]:
        segments = [part for part in Path(namespace).parts if part not in ("", ".")]
        if not segments or any(part == ".." for part in segments):
            raise ValueError(f"invalid memory namespace: {namespace!r}")
        for segment in segments:
            self._validate_segment(segment)
        return segments

    def _validate_segment(self, segment: str) -> None:
        if not self._SAFE_SEGMENT.fullmatch(segment):
            raise ValueError(f"invalid memory path segment: {segment!r}")

    @staticmethod
    def _is_shared_namespace(namespace: str) -> bool:
        return namespace == "shared" or namespace.startswith("shared/")

    def _tokens(self, value: str) -> list[str]:
        return [token.lower() for token in self._TOKEN.findall(value)]

    def _score(
        self, query: str, query_tokens: list[str], content: str, *, filename: str = ""
    ) -> float:
        lowered = content.lower()
        # 正文：完整查询匹配 +4，每个 token 出现一次 +1
        score = 4.0 if query.lower() in lowered else 0.0
        for token in query_tokens:
            score += lowered.count(token)

        # 标题权重 ×3：标题行已被正文统计过一次，此处补 ×2 额外权重
        heading_text = "\n".join(
            line for line in content.splitlines() if line.lstrip().startswith("#")
        ).lower()
        if heading_text:
            if query.lower() in heading_text:
                score += 8.0  # 4.0 × 2 额外 → 总计 ×3
            for token in query_tokens:
                score += heading_text.count(token) * 2  # 每词 +2 额外 → 总计 ×3

        # 文件名权重 ×2：文件名不在正文中，直接给双倍权重
        if filename:
            fn = filename.lower()
            if query.lower() in fn:
                score += 8.0  # 4.0 × 2
            for token in query_tokens:
                score += fn.count(token) * 2  # 每词 ×2

        return score

    # ------------------------------------------------------------------ #
    # frontmatter 元数据
    # ------------------------------------------------------------------ #
    def _with_frontmatter(
        self,
        path: Path,
        body: str,
        importance: int,
        confidence: int = 3,
        tags: Iterable[str] | None = None,
        source: str = "",
        type: str = "memory",
    ) -> str:
        """把元数据包成 YAML frontmatter 拼在正文前；覆盖写时保留原 created。"""
        now = datetime.now().isoformat(timespec="seconds")
        created = now
        if path.exists():
            try:
                _, old_meta = self._split_frontmatter(path.read_text(encoding="utf-8"))
                created = old_meta.get("created") or now
            except OSError:
                pass
        meta = {
            "created": created,
            "updated": now,
            "importance": max(0, min(5, int(importance))),
            "confidence": max(0, min(5, int(confidence))),
            "tags": sorted({str(t) for t in (tags or [])}),
            "source": source or "",
            "type": type if type in MEMORY_TYPES else "memory",
        }
        dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip()
        return f"---\n{dumped}\n---\n\n{body}"

    @classmethod
    def _split_frontmatter(cls, content: str) -> tuple[str, dict[str, Any]]:
        """把 Markdown 拆成 (正文, 元数据)；无/损坏 frontmatter 时元数据为空。"""
        if not content.startswith("---"):
            return content, {}
        text = content.replace("\r\n", "\n")
        if not text.startswith("---\n"):
            return content, {}
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            return content, {}
        try:
            meta = yaml.safe_load(parts[1])
        except yaml.YAMLError:  # pragma: no cover - 手写库可能有不合法 frontmatter
            meta = None
        return parts[2], meta if isinstance(meta, dict) else {}
