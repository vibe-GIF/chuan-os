"""Gateway 七大组件（ADR-012）—— 幕僚长控制平面。

从单一 RuntimeSupervisor 逐步拆分为七个独立组件，借鉴 OpenClaw Gateway：
① Message Router、② Session Manager、③ Agent Spawner、④ Skill Dispatcher、
⑤ Memory Operations、⑥ Heartbeat、⑦ Cron。

每个组件通过 `RuntimeSupervisor`（TYPE_CHECKING 引用）访问共享运行时状态，
避免循环导入；RuntimeSupervisor 只做组装与协调。
"""

from chuan.gateway.agent_spawner import AgentSpawner
from chuan.gateway.cron import CronManager
from chuan.gateway.heartbeat import Heartbeat
from chuan.gateway.memory_ops import MemoryOperations
from chuan.gateway.message_router import MessageRouter
from chuan.gateway.session_manager import SessionManager
from chuan.gateway.skill_dispatcher import SkillDispatcher

__all__ = [
    "MessageRouter",
    "SessionManager",
    "AgentSpawner",
    "SkillDispatcher",
    "MemoryOperations",
    "Heartbeat",
    "CronManager",
]