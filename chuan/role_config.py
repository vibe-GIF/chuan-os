"""N40/N41 岗位 agent 实例声明式配置（config.yaml → role_instances）。

「按任务复杂度选实例 + 动态实例池」的声明式方案：
- ``config.yaml`` 的 ``role_instances`` 段声明实例（RoleAgentConfig：brain/tools/
  system_prompt）与复杂度档位映射（simple/medium/heavy → 实例 id，角色可覆盖）；
- ``role_instances.pool`` 段声明动态实例池（N41：min_instances/max_instances/
  idle_ttl），驱动岗位自动扩缩容（扩容上限 + 空闲回收下限）；
- 运行时把声明解析为 ``RoleInstanceConfig``，岗位按任务复杂度分级取对应实例
  （未配置/空段 → 全默认实例，零开销旁路，向后兼容 1:1 行为）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from chuan.role import RoleAgentConfig, RolePoolConfig

# 复杂度档位（简单/中等/重型）缺省都走默认实例
_DEFAULT_TIERS = {"simple": "default", "medium": "default", "heavy": "default"}


@dataclass
class RoleInstanceConfig:
    """``config.yaml → role_instances`` 的解析结果（N40/N41）。"""

    # 全局复杂度档位 → 实例 id
    tiers: dict[str, str] = field(default_factory=dict)
    # 角色级覆盖：role → 复杂度档位 → 实例 id
    roles: dict[str, dict[str, str]] = field(default_factory=dict)
    # 实例声明：instance_id → 按实例配置（工具/模型/系统提示词/会话存档）
    instances: dict[str, RoleAgentConfig] = field(default_factory=dict)
    # N41 动态实例池配置（容量 + 空闲回收 TTL）；None = 关闭自动扩缩容
    pool: RolePoolConfig | None = None

    def tier_for(self, role: str) -> dict[str, str]:
        """取某角色的复杂度档位映射（角色覆盖优先，否则全局；缺省回 default）。"""
        merged = dict(_DEFAULT_TIERS)
        merged.update(self.tiers)
        merged.update(self.roles.get(role) or {})
        return merged


def load_role_instances(
    config_path: str | Path,
    brains: Any = None,
) -> RoleInstanceConfig:
    """解析 config.yaml 的 ``role_instances`` 段。缺段/空/解析失败 → 全默认。

    ``brain`` 名用 brains registry 解析为模型（``brains.get(name).model``），
    取不到则实例保持 model=None（沿用 persona 大脑）。工具/系统提示词透传
    ``RoleAgentConfig``。全程旁路：配置问题不抛错，退回默认实例。
    """
    cfg = RoleInstanceConfig()
    path = Path(config_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    if not path.exists():
        return cfg
    try:
        data = (
            yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        ).get("role_instances") or {}
    except (OSError, yaml.YAMLError):
        return cfg

    cfg.tiers = {
        str(k): str(v) for k, v in (data.get("tiers") or {}).items()
    }
    for role, tiers in (data.get("roles") or {}).items():
        if isinstance(tiers, dict):
            cfg.roles[str(role)] = {
                str(k): str(v) for k, v in tiers.items()
            }

    for name, spec in (data.get("instances") or {}).items():
        if not isinstance(spec, dict):
            continue
        model: Any = None
        brain = spec.get("brain")
        if brain and brains is not None:
            b = brains.get(brain)
            model = getattr(b, "model", None)
        tools = [str(t) for t in (spec.get("tools") or [])] or None
        cfg.instances[str(name)] = RoleAgentConfig(
            tools=tools,
            model=model,
            system_prompt=str(spec.get("system_prompt") or ""),
        )

    # N41 动态实例池（自动扩缩容）：缺省字段回 RolePoolConfig 默认值
    pool_spec = data.get("pool")
    if isinstance(pool_spec, dict):
        cfg.pool = RolePoolConfig(
            min_instances=int(pool_spec.get("min_instances", 1)),
            max_instances=int(pool_spec.get("max_instances", 3)),
            idle_ttl=float(pool_spec.get("idle_ttl", 300.0)),
        )
    return cfg
