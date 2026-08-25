"""N45 任务队列（chuan/queue.py）测试。

不连真实 Redis：Redis Streams 用 FakeRedis 注入（xadd/xlen/xgroup_create/
xreadgroup/xack/xpending/xautoclaim）；降级路径用端口 1。
默认 config enabled:false → no-op（封闭）。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from chuan.queue import TaskQueue

_ROOT = Path(__file__).resolve().parent.parent


class FakeRedis:
    """极简 redis 客户端替身：ping + Streams（消费者组语义）。"""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = {}  # stream -> [(id, fields)]
        self.pel: dict[str, dict[str, dict]] = {}            # stream -> {id: fields}（未确认）
        self._seq = 0

    def ping(self) -> bool:
        return True

    # ---- Streams ----
    def xadd(self, stream: str, fields: dict) -> str:
        self._seq += 1
        msg_id = f"{self._seq}-0"
        self.streams.setdefault(stream, []).append((msg_id, dict(fields)))
        return msg_id

    def xlen(self, stream: str) -> int:
        return len(self.streams.get(stream, []))

    def xdel(self, stream: str, *ids) -> None:
        entries = self.streams.get(stream, [])
        self.streams[stream] = [(mid, f) for mid, f in entries if mid not in ids]

    def xgroup_create(self, stream: str, group: str, id: str = "0", mkstream: bool = False) -> bool:
        self.streams.setdefault(stream, [])
        return True

    def xreadgroup(self, group: str, consumer: str, streams: dict, count: int = 1):
        stream = next(iter(streams))
        entries = self.streams.get(stream, [])
        if not entries:
            return None
        msg_id, fields = entries[0]  # 认领不删流（真实 Redis 语义）
        self.pel.setdefault(stream, {})[msg_id] = dict(fields)
        return [[stream, [(msg_id, fields)]]]

    def xack(self, stream: str, group: str, *ids) -> None:
        for mid in ids:
            self.pel.get(stream, {}).pop(mid, None)

    def xpending(self, stream: str, group: str):
        count = len(self.pel.get(stream, {}))
        if not count:
            return []
        ids = sorted(self.pel[stream])
        return [count, ids[0], ids[-1], [["consumer", 1]]]

    def xautoclaim(self, stream: str, group: str, consumer: str, min_idle: int, start_id: str = "0-0", count: int = 100):
        messages = [(mid, fields) for mid, fields in self.pel.get(stream, {}).items()]
        messages = messages[:count]
        next_id = messages[-1][0] if messages else "0-0"
        return [next_id, messages, []]


def _write_cfg(tmp_path: Path, *, enabled: bool, host: str = "127.0.0.1", port: int = 6379, max_retries: int = 3) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        yaml.safe_dump(
            {"bus": {"enabled": enabled, "host": host, "port": port, "queue": {"max_retries": max_retries}}}
        ),
        encoding="utf-8",
    )
    return p


# ── 默认关闭 ─────────────────────────────────────────────


def test_queue_disabled_by_default_noop(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, enabled=False)
    q = TaskQueue(config_path=cfg)
    assert q._enabled is False
    tid = q.submit("bg", {"task": "x"})
    assert tid
    assert q.pending("bg") == 0
    assert q.pop("bg") is None


# ── 内存后端 ─────────────────────────────────────────────


def test_queue_memory_fifo_roundtrip() -> None:
    q = TaskQueue(backend="memory")
    t1 = q.submit("bg", {"n": 1})
    t2 = q.submit("bg", {"n": 2})
    assert q.pending("bg") == 2

    task_id, payload, meta = q.pop("bg")
    assert task_id == t1 and payload == {"n": 1} and meta["retries"] == 0
    task_id, payload, _ = q.pop("bg")
    assert task_id == t2 and payload == {"n": 2}
    assert q.pending("bg") == 0
    assert q.pop("bg") is None


def test_queue_memory_requeue_retries() -> None:
    q = TaskQueue(backend="memory")
    q._max_retries = 2
    tid = q.submit("bg", {"n": 1})
    task_id, payload, meta = q.pop("bg")
    assert q.requeue("bg", meta.get("stream_id", ""), task_id, payload, meta["retries"]) is True
    assert q.pending("bg") == 1  # 重投回队尾
    _, _, meta2 = q.pop("bg")
    assert meta2["retries"] == 1
    # 再失败两次耗尽 → 丢弃
    assert q.requeue("bg", "", task_id, payload, meta2["retries"]) is True  # retries 2
    _, _, meta3 = q.pop("bg")
    assert q.requeue("bg", "", task_id, payload, meta3["retries"]) is False  # 超过上限丢弃
    assert q.pending("bg") == 0


def test_queue_memory_separate_queues() -> None:
    q = TaskQueue(backend="memory")
    q.submit("a", {"x": 1})
    q.submit("b", {"y": 2})
    assert q.pending("a") == 1 and q.pending("b") == 1
    task_id, payload, _ = q.pop("a")
    assert payload == {"x": 1}


# ── Redis Streams 后端（FakeRedis 注入）──────────────────


def test_queue_fake_redis_submit_pop_ack() -> None:
    fake = FakeRedis()
    q = TaskQueue(backend=fake)
    tid = q.submit("bg", {"n": 1})
    assert fake.streams["chuan:queue:bg"]  # 已落到 Redis Stream
    assert q.pending("bg") == 1

    task_id, payload, meta = q.pop("bg")
    assert task_id == tid and payload == {"n": 1}
    assert meta["stream_id"]
    assert q.pending("bg") == 1  # 认领未确认仍在 PEL
    q.ack("bg", meta["stream_id"])
    assert q.pending("bg") == 0  # 确认后清空


def test_queue_fake_redis_requeue_and_drop() -> None:
    fake = FakeRedis()
    q = TaskQueue(backend=fake)
    q._max_retries = 1
    tid = q.submit("bg", {"n": 1})
    task_id, payload, meta = q.pop("bg")
    # 第一次失败 → 重投
    assert q.requeue("bg", meta["stream_id"], task_id, payload, meta["retries"]) is True
    task_id, payload, meta2 = q.pop("bg")
    assert meta2["retries"] == 1
    # 第二次失败（超过上限）→ 丢弃
    assert q.requeue("bg", meta2["stream_id"], task_id, payload, meta2["retries"]) is False
    assert q.pending("bg") == 0


def test_queue_fake_redis_recover_stuck_pending() -> None:
    fake = FakeRedis()
    q = TaskQueue(backend=fake)
    q.submit("bg", {"n": 1})
    q.pop("bg")  # 认领后崩溃（未 ack）→ 留在 PEL
    recovered = q.recover("bg", min_idle_seconds=1)
    assert len(recovered) == 1
    task_id, payload, meta = recovered[0]
    assert payload == {"n": 1}
    q.ack("bg", meta["stream_id"])  # 处理后确认
    assert q.pending("bg") == 0


# ── 降级 ─────────────────────────────────────────────────


def test_queue_redis_unavailable_falls_back_to_memory(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path, enabled=True, port=1)
    q = TaskQueue(config_path=cfg)
    assert q._redis is None
    tid = q.submit("bg", {"n": 1})
    assert q.pending("bg") == 1
    task_id, payload, _ = q.pop("bg")
    assert task_id == tid and payload == {"n": 1}
    assert q.stats()["backend"] == "memory"
