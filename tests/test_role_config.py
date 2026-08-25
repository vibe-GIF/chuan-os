"""N40 按任务复杂度选实例 —— config.yaml 声明式配置（role_instances）测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chuan.role import PersonaRole, RoleAgentConfig, SubTask
from chuan.role_config import RoleInstanceConfig, load_role_instances

from tests.test_role import FakeAgent, FakeModel, _Persona, _PARALLEL_JSON
from tests.test_role import _N38Pool


# ── load_role_instances：config.yaml 解析 ─────────────


def _write_config(tmp_path: Path, role_instances: dict) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"role_instances": role_instances}), encoding="utf-8")
    return cfg


class _Brain:
    def __init__(self, model: object) -> None:
        self.model = model


class _Brains:
    """假 brain registry：name → Brain（含 model）。"""

    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping

    def get(self, name: str) -> _Brain | None:
        return self._mapping.get(name)


def test_load_role_instances_empty_when_missing(tmp_path: Path) -> None:
    """缺 role_instances 段 → 全默认（tier_for 全回 default）。"""
    cfg = tmp_path / "empty.yaml"
    cfg.write_text(yaml.safe_dump({}), encoding="utf-8")
    rc = load_role_instances(cfg, _Brains({}))
    assert rc.instances == {}
    assert rc.tier_for("any_role") == {
        "simple": "default", "medium": "default", "heavy": "default"
    }


def test_load_role_instances_missing_file_returns_defaults(tmp_path: Path) -> None:
    rc = load_role_instances(tmp_path / "nonexistent.yaml", _Brains({}))
    assert rc.instances == {}
    assert rc.tiers == {}


def test_load_role_instances_parses_tiers_instances_roles(tmp_path: Path) -> None:
    model = object()
    path = _write_config(tmp_path, {
        "tiers": {"heavy": "coding"},
        "instances": {
            "coding": {
                "brain": "cloud_coding",
                "tools": ["bash", "code_execution"],
                "system_prompt": "你是资深工程师",
            }
        },
        "roles": {"programmer": {"heavy": "coding"}},
    })
    rc = load_role_instances(path, _Brains({"cloud_coding": _Brain(model)}))
    assert rc.tiers == {"heavy": "coding"}
    coding = rc.instances["coding"]
    assert coding.model is model  # brain 名 → 解析为模型
    assert coding.tools == ["bash", "code_execution"]
    assert coding.system_prompt == "你是资深工程师"
    assert rc.roles["programmer"]["heavy"] == "coding"


def test_tier_for_merges_role_override_and_global() -> None:
    rc = RoleInstanceConfig(
        tiers={"heavy": "coding"},
        roles={"programmer": {"heavy": "engineer"}},
        instances={},
    )
    # 角色覆盖优先；未覆盖的档位回全局/默认
    assert rc.tier_for("programmer")["heavy"] == "engineer"
    assert rc.tier_for("programmer")["simple"] == "default"
    assert rc.tier_for("other_role")["heavy"] == "coding"


def test_load_role_instances_unknown_brain_keeps_model_none(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {
        "instances": {"coding": {"brain": "不存在的脑"}}
    })
    rc = load_role_instances(path, _Brains({}))
    assert rc.instances["coding"].model is None  # 取不到 → 沿用 persona 大脑


# ── N41 动态实例池：pool 配置段解析 ───────────────


def test_load_role_instances_parses_pool(tmp_path: Path) -> None:
    """role_instances.pool 段 → RolePoolConfig（容量 + 空闲 TTL）。"""
    path = _write_config(tmp_path, {
        "pool": {"min_instances": 2, "max_instances": 5, "idle_ttl": 60},
    })
    rc = load_role_instances(path, _Brains({}))
    assert rc.pool is not None
    assert rc.pool.min_instances == 2
    assert rc.pool.max_instances == 5
    assert rc.pool.idle_ttl == 60


def test_load_role_instances_pool_partial_fields_defaults(tmp_path: Path) -> None:
    """pool 段缺省字段回 RolePoolConfig 默认值。"""
    path = _write_config(tmp_path, {"pool": {"idle_ttl": 120}})
    rc = load_role_instances(path, _Brains({}))
    assert rc.pool is not None
    assert rc.pool.min_instances == 1  # 默认下限
    assert rc.pool.max_instances == 3  # 默认上限
    assert rc.pool.idle_ttl == 120


def test_load_role_instances_missing_pool_is_none(tmp_path: Path) -> None:
    """未配置 pool 段 → pool 为 None（关闭自动扩缩容）。"""
    path = _write_config(tmp_path, {"tiers": {"heavy": "coding"}})
    rc = load_role_instances(path, _Brains({}))
    assert rc.pool is None


# ── 复杂度分级 + 选实例（PersonaRole）───────────────


def _role_with_config(
    pool: _N38Pool, rc: RoleInstanceConfig | None
) -> PersonaRole:
    return PersonaRole(
        _Persona(), pool, planner_model=FakeModel(_PARALLEL_JSON),
        instance_config=rc,
    )


def test_classify_complexity() -> None:
    role = _role_with_config(_N38Pool(model=FakeModel(_PARALLEL_JSON)), None)
    assert role._classify_complexity("帮我写个 Python 脚本排序") == "heavy"  # 脚本
    assert role._classify_complexity("修复登录页的 bug") == "heavy"  # 修复
    assert role._classify_complexity("先查天气，然后写总结") == "medium"  # 步骤词
    assert role._classify_complexity("今天天气如何") == "simple"  # 短问答


async def test_resolve_tier_instance_uses_declared_config() -> None:
    """N40：heavy 档位 → 声明式 coding 实例（按配置创建并记录）。"""
    model = FakeModel("x")
    rc = RoleInstanceConfig(
        tiers={"heavy": "coding"},
        instances={"coding": RoleAgentConfig(
            model=model, tools=["t1"], system_prompt="编码人设",
        )},
    )
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = _role_with_config(pool, rc)
    agent = role._resolve_tier_instance("heavy")
    assert role._agents["coding"] is agent
    assert pool.instance_calls[0]["model"] is model
    assert pool.instance_calls[0]["tools"] == ["t1"]
    assert pool.instance_calls[0]["system_prompt"] == "编码人设"
    assert role._agent_configs["coding"] == rc.instances["coding"]


async def test_resolve_tier_instance_falls_back_without_config() -> None:
    """未配置声明式方案 → 默认实例（向后兼容 1:1）。"""
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = _role_with_config(pool, None)
    assert role._resolve_tier_instance("heavy") is role._ensure_default_agent()
    assert role._resolve_tier_instance("simple") is role._ensure_default_agent()


async def test_resolve_tier_instance_missing_instance_falls_back() -> None:
    """档位映射到未声明的实例 id → 回退默认实例（旁路）。"""
    rc = RoleInstanceConfig(tiers={"heavy": "不存在的实例"}, instances={})
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = _role_with_config(pool, rc)
    assert role._resolve_tier_instance("heavy") is role._ensure_default_agent()


async def test_dispatch_heavy_task_uses_declared_instance() -> None:
    """N40 集成：重型单任务走声明式重型实例，而非默认实例。"""
    model = FakeModel("x")
    rc = RoleInstanceConfig(
        tiers={"heavy": "coding"},
        instances={"coding": RoleAgentConfig(
            model=model, system_prompt="编码人设",
        )},
    )
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = _role_with_config(pool, rc)
    reply = await role.dispatch("帮我写个 Python 脚本排序")  # heavy + 短 → 单 agent
    assert role._agents.get("coding") is not None  # 声明式实例被创建
    assert pool.default.calls == []  # 没走默认实例
    assert "结果:" in reply


async def test_dispatch_simple_task_uses_default() -> None:
    """N40：simple 任务仍走默认实例（tiers.simple=default）。"""
    rc = RoleInstanceConfig(
        tiers={"heavy": "coding"},
        instances={"coding": RoleAgentConfig(model=FakeModel("x"))},
    )
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = _role_with_config(pool, rc)
    await role.dispatch("今天天气如何")  # simple
    assert "coding" not in role._agents  # 未创建重型实例
    assert len(pool.default.calls) == 1  # 默认实例执行


def test_ensure_configured_instance_idempotent() -> None:
    """同一声明式实例按 id 复用（不重复 spawn）。"""
    rc = RoleInstanceConfig(
        tiers={"heavy": "coding"},
        instances={"coding": RoleAgentConfig(model=FakeModel("x"))},
    )
    pool = _N38Pool(model=FakeModel(_PARALLEL_JSON))
    role = _role_with_config(pool, rc)
    role._resolve_tier_instance("heavy")
    role._resolve_tier_instance("heavy")
    assert pool.instance_spawns == 1  # 只 spawn 一次
