"""N45 任务队列 —— Redis Streams 消费者组后端 + 进程内内存兜底。

给「后台委派 / 例行任务」加一层可靠队列：任务落 Redis Stream（重启不丢、
跨进程可见），消费走消费者组（认领不删除，崩溃后可 ``recover`` 收回）。
设计对齐项目「旁路增强、真相不动、故障静默降级」：

- ``submit(queue, payload)``：入队，返回 task_id（Redis XADD / 内存 deque）；
- ``pop(queue)``：认领一条待处理任务（Redis XREADGROUP / 内存弹出一条），
  返回 (task_id, payload, meta)；meta 含 stream_id / retries；
- ``ack(queue, stream_id)``：处理成功确认（XACK；内存弹走即视为确认）；
- ``requeue(...)``：处理失败重试——未超上限重投（XADD 新记录 + XACK 旧的），
  超上限丢弃（队列是「能删能重建的协调层」，不做无限重试）；
- ``recover(min_idle)``：启动/崩溃后收回卡死的 pending 任务（XAUTOCLAIM）；
- 任何 Redis 故障 → 自动降级进程内内存队列（不抛错，不阻断主流程）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TaskQueue:
    """可靠任务队列（Redis Streams 消费者组 + 内存兜底）。

    ``backend``:
        - "auto"（默认）：读 config.yaml 的 bus 段——enabled 才启用；
        - "memory"：强制进程内内存后端（测试 / 无 Redis 兜底）；
        - 传入 redis 客户端实例：直接用（测试注入 FakeRedis）。
    """

    def __init__(
        self,
        config_path: str | Path = "config/config.yaml",
        *,
        backend: Any = "auto",
        prefix: str = "chuan:",
    ) -> None:
        cfg = self._load_config(config_path)
        queue_cfg = cfg.get("queue") or {}
        self._prefix = str(cfg.get("prefix") or prefix)
        self._group = str(queue_cfg.get("group") or "chuan-workers")
        self._consumer = f"chuan-{os.getpid()}"
        self._max_retries = int(queue_cfg.get("max_retries") or 3)
        self._memory: dict[str, deque[dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._redis: Any = None
        self._enabled: bool = False
        self._groups_ready: set[str] = set()

        if backend == "auto":
            if not cfg.get("enabled"):
                return  # 未启用 → no-op
            self._enabled = True
            self._connect_redis(cfg)
        elif backend == "memory":
            self._enabled = True
        else:  # 注入的 redis 客户端（测试用 FakeRedis / 真实实例）
            self._enabled = True
            self._redis = backend

    # ------------------------------------------------------------------ #
    # 入队 / 消费
    # ------------------------------------------------------------------ #
    def submit(self, queue: str, payload: dict[str, Any], *, retries: int = 0) -> str:
        """入队一个任务，返回 task_id。"""
        queue = str(queue)
        task_id = uuid.uuid4().hex[:12]
        record = {
            "task_id": task_id,
            "payload": json.dumps(payload, ensure_ascii=False),
            "retries": str(retries),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if not self._enabled:
            return task_id
        if self._redis is not None:
            try:
                self._ensure_group(queue)
                self._redis.xadd(self._stream(queue), record)
                return task_id
            except Exception:  # noqa: BLE001 - Redis 故障降级内存
                self._redis = None
        with self._lock:
            self._memory.setdefault(queue, deque()).append(record)
        return task_id

    def pop(self, queue: str) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        """认领一条任务；返回 (task_id, payload, meta)。无任务返回 None。"""
        queue = str(queue)
        if not self._enabled:
            return None
        if self._redis is not None:
            try:
                self._ensure_group(queue)
                result = self._redis.xreadgroup(
                    self._group, self._consumer, {self._stream(queue): ">"}, count=1
                )
                if not result:
                    return None
                stream_id, fields = result[0][1][0]
                return self._parse(stream_id, fields)
            except Exception:  # noqa: BLE001 - Redis 故障降级内存
                self._redis = None
        with self._lock:
            dq = self._memory.get(queue)
            if not dq:
                return None
            record = dq.popleft()
            return record["task_id"], json.loads(record["payload"]), {"retries": int(record.get("retries") or 0)}

    # ------------------------------------------------------------------ #
    # 确认 / 重试
    # ------------------------------------------------------------------ #
    def ack(self, queue: str, stream_id: str) -> None:
        """确认处理成功（Redis XACK + XDEL 移除出队列；内存已弹走，no-op）。"""
        if not self._enabled or self._redis is None:
            return
        try:
            self._redis.xack(self._stream(str(queue)), self._group, stream_id)
            self._redis.xdel(self._stream(str(queue)), stream_id)  # 队列非日志，确认即出队
        except Exception:  # noqa: BLE001, S110 - 确认失败是旁路
            pass

    def requeue(
        self,
        queue: str,
        stream_id: str,
        task_id: str,
        payload: dict[str, Any],
        retries: int,
        *,
        reason: str = "",
    ) -> bool:
        """处理失败：未超上限重投并返回 True；超上限丢弃返回 False。

        Redis：XACK 旧记录 + XADD 新记录（retries+1）。
        内存：直接把记录塞回队尾（retries+1）。
        """
        queue = str(queue)
        if not self._enabled:
            return False
        new_retries = retries + 1
        if new_retries > self._max_retries:
            self.ack(queue, stream_id)  # 丢弃前清掉（内存已弹走）
            return False
        if self._redis is not None:
            try:
                self.ack(queue, stream_id)
                self.submit(queue, payload, retries=new_retries)
                return True
            except Exception:  # noqa: BLE001 - Redis 故障降级内存
                self._redis = None
        with self._lock:
            self._memory.setdefault(queue, deque()).append(
                {
                    "task_id": task_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "retries": str(new_retries),
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
        return True

    # ------------------------------------------------------------------ #
    # 断点恢复 / 可观测
    # ------------------------------------------------------------------ #
    def pending(self, queue: str) -> int:
        """待处理任务数（Redis XLEN 未处理完消息 / 内存 deque 长度）。

        认领未确认（in-flight）也计入；确认（ack）时 XDEL 出队才扣减。
        """
        queue = str(queue)
        if not self._enabled:
            return 0
        if self._redis is not None:
            try:
                return int(self._redis.xlen(self._stream(queue)))
            except Exception:  # noqa: BLE001 - Redis 故障降级内存
                self._redis = None
        with self._lock:
            return len(self._memory.get(queue, ()))

    def recover(self, queue: str, min_idle_seconds: float = 30.0) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        """收回超时未确认的 pending 任务（崩溃恢复）。

        内存后端无从收（弹出即处理），返回空列表。
        """
        queue = str(queue)
        if not self._enabled or self._redis is None:
            return []
        try:
            self._ensure_group(queue)
            _next, messages, _deleted = self._redis.xautoclaim(
                self._stream(queue),
                self._group,
                self._consumer,
                int(min_idle_seconds * 1000),
                start_id="0-0",
                count=100,
            )
            return [self._parse(stream_id, fields) for stream_id, fields in messages]
        except Exception:  # noqa: BLE001 - Redis 故障降级内存
            self._redis = None
            return []

    def stats(self) -> dict[str, Any]:
        """队列状态：后端 / 队列数 / 上限重试次数。"""
        with self._lock:
            n_queues = len(self._memory)
        return {
            "enabled": self._enabled,
            "backend": (
                "redis"
                if self._redis is not None
                else ("memory" if self._enabled else "disabled")
            ),
            "queues": n_queues,
            "max_retries": self._max_retries,
            "group": self._group,
        }

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _stream(self, queue: str) -> str:
        return f"{self._prefix}queue:{queue}"

    def _ensure_group(self, queue: str) -> None:
        stream = self._stream(queue)
        if stream in self._groups_ready:
            return
        try:
            self._redis.xgroup_create(stream, self._group, id="0", mkstream=True)
        except Exception:  # noqa: BLE001 - BUSYGROUP 等已存在即视为就绪
            pass
        self._groups_ready.add(stream)

    @staticmethod
    def _parse(stream_id: str, fields: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        task_id = str(fields.get("task_id", stream_id))
        try:
            payload = json.loads(fields.get("payload", "{}"))
        except (TypeError, json.JSONDecodeError):  # noqa: S110
            payload = {}
        retries = int(fields.get("retries") or 0)
        return task_id, payload, {"stream_id": stream_id, "retries": retries}

    def _connect_redis(self, cfg: dict[str, Any]) -> None:
        try:
            import redis as redis_py

            client = redis_py.Redis(
                host=str(cfg.get("host") or "127.0.0.1"),
                port=int(cfg.get("port") or 6379),
                db=int(cfg.get("db") or 0),
                # RESP2：redis-py 8 默认 RESP3 握手包大，振荡网络下易超时
                protocol=2,
                socket_connect_timeout=5.0,
                socket_timeout=5.0,
                decode_responses=True,
            )
            client.ping()
            self._redis = client
        except Exception:  # noqa: BLE001 - Redis 不可达 → 内存兜底
            self._redis = None

    @staticmethod
    def _load_config(config_path: str | Path) -> dict[str, Any]:
        config = Path(config_path)
        if not config.is_absolute():
            config = _project_root() / config
        data: dict[str, Any] = {}
        if config.exists():
            try:
                with config.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):  # pragma: no cover
                data = {}
        return data.get("bus", {}) or {}


# ------------------------------------------------------------------ #
# 进程级默认任务队列（共享，懒加载）
# ------------------------------------------------------------------ #
_default_queue: TaskQueue | None = None
_default_queue_lock = threading.Lock()


def get_queue(config_path: str | Path = "config/config.yaml") -> TaskQueue:
    """取进程级默认任务队列（懒加载、幂等）。"""
    global _default_queue
    with _default_queue_lock:
        if _default_queue is None:
            _default_queue = TaskQueue(config_path=config_path)
    return _default_queue
