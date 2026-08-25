"""N45 事件总线（chuan/bus.py）测试。

不连真实 Redis：Redis 后端用 FakeRedis 注入；降级路径用端口 1（立即拒连）。
默认 config enabled:false → no-op（封闭）。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from chuan.bus import EventBus, make_event

_ROOT = Path(__file__).resolve().parent.parent


class FakeRedis:
    """极简 redis 客户端替身：ping / publish。"""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def ping(self) -> bool:
        return True

    def publish(self, channel: str, payload) -> None:
        self.published.append((channel, payload))


def _write_cfg(tmp_path: Path, *, enabled: bool, host: str = "127.0.0.1", port: int = 6379) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        yaml.safe_dump({"bus": {"enabled": enabled, "host": host, "port": port}}),
        encoding="utf-8",
    )
    return p


# ── 默认关闭 ─────────────────────────────────────────────


def test_bus_disabled_by_default_noop(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, enabled=False)
    bus = EventBus(config_path=cfg)
    assert bus._enabled is False
    got = []

    bus.publish("agent.task", make_event("t", "src"))
    bus.subscribe("agent.task", lambda t, e: got.append(e))
    bus.publish("agent.task", make_event("t", "src"))
    assert got == []  # 未启用 → 不发布也不分发
    assert bus.stats()["backend"] == "disabled"


# ── 内存后端 ─────────────────────────────────────────────


def test_bus_memory_publish_dispatch_sync() -> None:
    bus = EventBus(backend="memory")
    got: list[tuple[str, dict]] = []
    bus.subscribe("agent.task", lambda t, e: got.append((t, e)))
    ev = make_event("delegate.done", "harness:pi", {"task_id": "t1"})
    bus.publish("agent.task", ev)
    assert len(got) == 1
    topic, event = got[0]
    assert topic == "agent.task"
    assert event["type"] == "delegate.done"
    assert event["source"] == "harness:pi"
    assert event["payload"] == {"task_id": "t1"}
    assert event["topic"] == "agent.task"  # 发布时回填 topic


def test_bus_memory_unsubscribe() -> None:
    bus = EventBus(backend="memory")
    got = []
    unsub = bus.subscribe("t", lambda t, e: got.append(e))
    bus.publish("t", {"a": 1})
    assert len(got) == 1
    unsub()
    bus.publish("t", {"a": 2})
    assert len(got) == 1  # 退订后不再收到


def test_bus_memory_topics_and_stats() -> None:
    bus = EventBus(backend="memory")
    bus.subscribe("a", lambda t, e: None)
    bus.subscribe("b", lambda t, e: None)
    bus.subscribe("b", lambda t, e: None)
    bus.publish("a", make_event("x", "src"))
    st = bus.stats()
    assert st["backend"] == "memory"
    assert st["topics"] == 2
    assert st["handlers"] == 3
    assert st["published"] == 1
    assert bus.topics() == ["a", "b"]


def test_bus_make_event_fields() -> None:
    ev = make_event("delegate.started", "harness", {"n": 1}, topic="agent.task")
    assert ev["type"] == "delegate.started"
    assert ev["source"] == "harness"
    assert ev["topic"] == "agent.task"
    assert ev["payload"] == {"n": 1}
    assert ev["event_id"]
    assert ev["timestamp"]


# ── Redis 后端（FakeRedis 注入）──────────────────────────


def test_bus_fake_redis_publish_broadcasts_and_dispatches_local() -> None:
    fake = FakeRedis()
    bus = EventBus(backend=fake)
    got = []
    bus.subscribe("agent.task", lambda t, e: got.append(e))
    bus.publish("agent.task", make_event("delegate.done", "h", {"task_id": "t1"}))

    # Redis 广播：带前缀的通道 + JSON 负载
    assert len(fake.published) == 1
    channel, payload = fake.published[0]
    assert channel == "chuan:event:agent.task"
    assert json.loads(payload)["type"] == "delegate.done"
    # 本进程订阅者同步收到
    assert len(got) == 1 and got[0]["payload"] == {"task_id": "t1"}


def test_bus_redis_unavailable_falls_back_to_memory(tmp_path: Path) -> None:
    """config 启用但 Redis 连不上（端口 1）→ 降级内存，本进程订阅仍可用。"""
    cfg = _write_cfg(tmp_path, enabled=True, port=1)
    bus = EventBus(config_path=cfg)
    assert bus._redis is None
    got = []
    bus.subscribe("t", lambda t, e: got.append(e))
    bus.publish("t", make_event("x", "src"))
    assert len(got) == 1
    assert bus.stats()["backend"] == "memory"
