"""N44 Redis TTL 缓存旁路（chuan/cache.py + 天气接线）测试。

不触网：Redis 后端用 FakeRedis 注入；天气接线 monkeypatch urlopen。
默认 config enabled:false → no-op（封闭）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import yaml

from chuan.cache import Cache

_ROOT = Path(__file__).resolve().parent.parent


class FakeRedis:
    """极简 redis 客户端替身：get/set/setex/ping/delete。"""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, val) -> None:
        self.data[key] = val

    def setex(self, key: str, ttl: int, val) -> None:
        self.data[key] = val

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def _write_cfg(tmp_path: Path, *, enabled: bool, host: str = "127.0.0.1", port: int = 6379) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        yaml.safe_dump({"cache": {"enabled": enabled, "host": host, "port": port, "default_ttl": 600}}),
        encoding="utf-8",
    )
    return p


# ── Cache 本体 ─────────────────────────────────────────


def test_cache_disabled_by_default_noop(tmp_path: Path) -> None:
    """config enabled:false（默认）→ no-op：get 恒 None、set 不写。"""
    cfg = _write_cfg(tmp_path, enabled=False)
    c = Cache(config_path=cfg)
    assert c._enabled is False
    c.set("k", "v")
    assert c.get("k") is None
    assert c._memory == {}


def test_cache_memory_backend_roundtrip() -> None:
    c = Cache(backend="memory")
    c.set("k", {"a": 1, "b": "中文"})
    assert c.get("k") == {"a": 1, "b": "中文"}
    assert c.get("missing") is None


def test_cache_memory_ttl_expiry() -> None:
    c = Cache(backend="memory", default_ttl=1.0)
    c.set("k", "v", ttl=0.05)
    assert c.get("k") == "v"
    time.sleep(0.1)
    assert c.get("k") is None  # 过期即失


def test_cache_fake_redis_backend() -> None:
    fake = FakeRedis()
    c = Cache(backend=fake)
    c.set("k", {"a": 1})
    # 数据落到 Redis（带前缀 + JSON 序列化）
    assert fake.data["chuan:k"] == json.dumps({"a": 1}, ensure_ascii=False)
    assert c.get("k") == {"a": 1}


def test_cache_redis_unavailable_falls_back_to_memory(tmp_path: Path) -> None:
    """config 启用但 Redis 连不上（端口 1 立即拒连）→ 降级进程内内存，不抛错。"""
    cfg = _write_cfg(tmp_path, enabled=True, port=1)
    c = Cache(config_path=cfg)
    assert c._redis is None  # 已降级
    c.set("k", "v")
    assert c.get("k") == "v"  # 内存兜底仍可用


# ── 天气 MCP 接线（cache-aside）────────────────────────


def _load_weather():
    spec = importlib.util.spec_from_file_location(
        "weather_server", str(_ROOT / "mcp_servers" / "weather_server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weather_server"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_weather_cache_hit_skips_network(monkeypatch) -> None:
    mod = _load_weather()
    c = Cache(backend="memory")
    mod._cache = c
    c.set("weather:北京", "北京：晴，温度 25°C，湿度 60%")

    def fail_urlopen(*args, **kwargs):  # 命中缓存时绝不应触网
        raise AssertionError("命中缓存不应发起网络请求")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    assert mod.get_weather("北京") == "北京：晴，温度 25°C，湿度 60%"


def test_weather_cache_writes_on_success_then_hits(monkeypatch) -> None:
    mod = _load_weather()
    c = Cache(backend="memory")
    mod._cache = c
    raw = (
        '{"current_condition":[{"temp_C":"25","humidity":"60",'
        '"weatherDesc":[{"value":"晴"}]}],'
        '"nearest_area":[{"areaName":[{"value":"北京"}]}]}'
    ).encode("utf-8")

    class FakeResp:
        def read(self):
            return raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: FakeResp())
    result = mod.get_weather("北京")
    assert "25" in result and "晴" in result
    assert c.get("weather:北京") == result  # 成功结果已缓存

    # 二次调用命中缓存，不触网
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触网")),
    )
    assert mod.get_weather("北京") == result
