"""N44 Redis TTL 缓存旁路（cache-aside 加速）—— Redis 后端 + 进程内内存兜底。

给外部 API 调用（天气 / 搜索等）加 TTL 缓存：命中免外呼、省时省钱、避开外部
不稳定（如天气服务超时）。设计对齐项目「旁路增强、真相不动、故障静默降级」：

- 默认 ``config.yaml`` 的 ``cache.enabled`` 为 false → no-op（零依赖零成本，测试封闭）；
- 置 true 后优先 Redis（redis-py）：连接/读写任何失败 → 自动降级进程内内存 TTL
  缓存（不抛错，主流程不受影响）；
- 缓存是「能删能重建的加速层」，绝不承担不可丢失的状态。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class Cache:
    """TTL 键值缓存（cache-aside）。

    ``backend``:
        - "auto"（默认）：读 config.yaml 的 cache 段——enabled 才启用；
        - "memory"：强制进程内内存后端（测试 / 无 Redis 时兜底）；
        - 传入 redis 客户端实例：直接用（测试注入 FakeRedis）。

    序列化为 JSON；get 未命中 / 已过期 / 未启用返回 None。
    """

    def __init__(
        self,
        config_path: str | Path = "config/config.yaml",
        *,
        backend: Any = "auto",
        default_ttl: float = 600.0,
    ) -> None:
        cfg = self._load_config(config_path)
        self._default_ttl = float(cfg.get("default_ttl") or default_ttl)
        self._prefix = str(cfg.get("prefix") or "chuan:")
        self._memory: dict[str, tuple[float | None, Any]] = {}
        self._redis: Any = None
        self._enabled: bool = False

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
    # 公开接口
    # ------------------------------------------------------------------ #
    def get(self, key: str) -> Any | None:
        """读缓存；未启用/未命中/过期返回 None。Redis 故障自动降级内存。"""
        if not self._enabled:
            return None
        if self._redis is not None:
            try:
                raw = self._redis.get(f"{self._prefix}{key}")
                if raw is None:
                    return None
                return json.loads(raw)
            except Exception:  # noqa: BLE001 - Redis 故障降级内存，不阻断
                self._redis = None
        return self._get_memory(key)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """写缓存；ttl 缺省取 config default_ttl（<=0 表示不过期）。"""
        if not self._enabled:
            return
        ttl = self._default_ttl if ttl is None else ttl
        if self._redis is not None:
            try:
                payload = json.dumps(value, ensure_ascii=False)
                if ttl and ttl > 0:
                    self._redis.setex(f"{self._prefix}{key}", int(ttl), payload)
                else:
                    self._redis.set(f"{self._prefix}{key}", payload)
                return
            except Exception:  # noqa: BLE001 - Redis 故障降级内存
                self._redis = None
        self._set_memory(key, value, ttl)

    def clear(self) -> None:
        """清空缓存（内存 + Redis 前缀键）。"""
        self._memory.clear()
        if self._redis is not None:
            try:
                for key in list(self._memory):
                    self._redis.delete(f"{self._prefix}{key}")
            except Exception:  # noqa: BLE001 - 清理失败不影响
                pass

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _connect_redis(self, cfg: dict[str, Any]) -> None:
        """尝试连 Redis 并 ping 探测；失败 → self._redis=None（内存兜底）。"""
        try:
            import redis as redis_py

            client = redis_py.Redis(
                host=str(cfg.get("host") or "127.0.0.1"),
                port=int(cfg.get("port") or 6379),
                db=int(cfg.get("db") or 0),
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=False,
            )
            client.ping()  # 探测连通；失败抛异常走内存兜底
            self._redis = client
        except Exception:  # noqa: BLE001 - Redis 不可达/未装 → 内存兜底
            self._redis = None

    def _get_memory(self, key: str) -> Any | None:
        item = self._memory.get(key)
        if item is None:
            return None
        expires, value = item
        if expires is not None and time.monotonic() >= expires:
            self._memory.pop(key, None)
            return None
        return value

    def _set_memory(self, key: str, value: Any, ttl: float) -> None:
        expires = time.monotonic() + ttl if ttl and ttl > 0 else None
        self._memory[key] = (expires, value)

    @staticmethod
    def _load_config(config_path: str | Path) -> dict[str, Any]:
        """读 config.yaml 的 cache 段（缺失返回空 dict）。"""
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
        return data.get("cache", {}) or {}


# ------------------------------------------------------------------ #
# 进程级默认缓存实例（天气/搜索等模块共享，懒加载）
# ------------------------------------------------------------------ #
_default_cache: Cache | None = None
_default_cache_lock = threading.Lock()


def get_cache(config_path: str | Path = "config/config.yaml") -> Cache:
    """取进程级默认缓存（懒加载、幂等）。"""
    global _default_cache
    with _default_cache_lock:
        if _default_cache is None:
            _default_cache = Cache(config_path=config_path)
    return _default_cache
