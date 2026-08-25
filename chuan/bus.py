"""N45 事件总线 —— Redis Pub/Sub 后端 + 进程内内存兜底。

为岗位间 / 任务间事件驱动协作提供发布订阅通道。设计对齐项目「旁路增强、
真相不动、故障静默降级」：

- ``publish(topic, event)``：同步发布，事件是 JSON 字典（标准体见 ``make_event``）；
- ``subscribe(topic, handler)``：注册**本进程**处理器，返回退订函数；本进程订阅者
  始终同步收到事件（内存分发，确定性、低延迟）；
- Redis 可用时额外 ``publish`` 到 Redis（跨进程可见）；其他进程可
  ``start_listener()`` 拉起监听线程接收本进程之外发来的事件；
- 任何 Redis 故障 → 自动降级纯内存（不抛错，不阻断主流程）；
- 事件总线是「能删能重建的协调层」，绝不承担不可丢失的状态（真相仍在
  SQLite / Markdown）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

# 事件处理器签名: (topic: str, event: dict) -> None
EventHandler = Callable[[str, dict[str, Any]], None]

# 监听通道模式（实际通道 = f"{prefix}event:{topic}"）
_LISTENER_PATTERN = "event:*"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def make_event(
    type_: str,
    source: str,
    payload: dict[str, Any] | None = None,
    *,
    topic: str = "",
) -> dict[str, Any]:
    """构造标准事件体：event_id / type / source / timestamp / topic / payload。"""
    return {
        "event_id": uuid.uuid4().hex[:12],
        "type": type_,
        "source": source,
        "topic": topic,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "payload": payload or {},
    }


class EventBus:
    """进程内发布 / 订阅事件总线（Redis Pub/Sub + 内存兜底）。

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
        self._prefix = str(cfg.get("prefix") or prefix)
        self._handlers: dict[str, set[EventHandler]] = {}
        self._lock = threading.RLock()
        self._redis: Any = None
        self._enabled: bool = False
        self._listener_started: bool = False
        self._stats: dict[str, int] = {"published": 0, "dispatched": 0, "dropped": 0}

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
    # 发布 / 订阅
    # ------------------------------------------------------------------ #
    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """发布事件到 topic。本进程订阅者同步收到；Redis 可用时广播跨进程。"""
        if not self._enabled:
            return
        topic = str(topic)
        event["topic"] = topic
        self._stats["published"] += 1
        if self._redis is not None:
            try:
                self._redis.publish(
                    f"{self._prefix}{_LISTENER_PATTERN.replace('*', topic)}",
                    json.dumps(event, ensure_ascii=False),
                )
            except Exception:  # noqa: BLE001 - Redis 故障降级内存
                self._redis = None
        self._dispatch_local(topic, event)

    def subscribe(self, topic: str, handler: EventHandler) -> Callable[[], None]:
        """注册本进程事件处理器；返回退订函数（幂等）。"""
        topic = str(topic)
        with self._lock:
            self._handlers.setdefault(topic, set()).add(handler)

        def unsubscribe() -> None:
            with self._lock:
                self._handlers.get(topic, set()).discard(handler)

        return unsubscribe

    # ------------------------------------------------------------------ #
    # 跨进程接收（可选，运行时启动）
    # ------------------------------------------------------------------ #
    def start_listener(self) -> None:
        """拉起后台监听线程，接收其他进程经 Redis 发布的事件（幂等）。"""
        if not self._enabled or self._redis is None or self._listener_started:
            return
        self._listener_started = True
        threading.Thread(
            target=self._listener_loop,
            name="chuan-event-listener",
            daemon=True,
        ).start()

    def stop_listener(self) -> None:
        """停止监听线程（守护线程，下次循环/断线时退出）。"""
        self._listener_started = False

    def _listener_loop(self) -> None:  # pragma: no cover - 真实 Redis 监听循环
        while self._listener_started:
            try:
                pubsub = self._redis.pubsub()
                pubsub.psubscribe(f"{self._prefix}{_LISTENER_PATTERN}")
                for msg in pubsub.listen():
                    if not self._listener_started:
                        return
                    if msg.get("type") != "pmessage":
                        continue
                    channel = msg.get("channel", "")
                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8", "ignore")
                    prefix = f"{self._prefix}event:"
                    topic = channel[len(prefix):] if channel.startswith(prefix) else ""
                    if not topic:
                        continue
                    data = msg.get("data")
                    try:
                        event = json.loads(data)
                    except (TypeError, json.JSONDecodeError):  # noqa: S110
                        continue
                    self._dispatch_local(topic, event)
            except Exception:  # noqa: BLE001 - 监听断线重连，不阻断主流程
                if not self._listener_started:
                    return
                time.sleep(1)

    # ------------------------------------------------------------------ #
    # 可观测
    # ------------------------------------------------------------------ #
    def topics(self) -> list[str]:
        """当前有订阅的 topic 列表（去重排序）。"""
        with self._lock:
            return sorted(self._handlers)

    def stats(self) -> dict[str, Any]:
        """总线状态：后端 / 订阅数 / 发布分发统计 / 监听是否启动。"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "backend": (
                    "redis"
                    if self._redis is not None
                    else ("memory" if self._enabled else "disabled")
                ),
                "topics": len(self._handlers),
                "handlers": sum(len(h) for h in self._handlers.values()),
                "listener": self._listener_started,
                **self._stats,
            }

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _dispatch_local(self, topic: str, event: dict[str, Any]) -> None:
        with self._lock:
            handlers = list(self._handlers.get(topic, ()))
        for handler in handlers:
            try:
                handler(topic, event)
                self._stats["dispatched"] += 1
            except Exception:  # noqa: BLE001, S110 - 单个处理器失败是旁路
                self._stats["dropped"] += 1

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
# 进程级默认事件总线（AgentHarness 等共享，懒加载）
# ------------------------------------------------------------------ #
_default_bus: EventBus | None = None
_default_bus_lock = threading.Lock()


def get_bus(config_path: str | Path = "config/config.yaml") -> EventBus:
    """取进程级默认事件总线（懒加载、幂等）。"""
    global _default_bus
    with _default_bus_lock:
        if _default_bus is None:
            _default_bus = EventBus(config_path=config_path)
    return _default_bus
