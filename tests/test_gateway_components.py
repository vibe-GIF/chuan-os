"""Gateway 七大组件（ADR-012）的单元测试。

覆盖：MessageRouter 纯逻辑（繁→简、天气兜底快速通道）、
Heartbeat 健康检查（唯一全新组件）、MemoryOperations 与 CronManager 的委托降级路径。
"""
from __future__ import annotations

from chuan.gateway.cron import CronManager
from chuan.gateway.heartbeat import Heartbeat
from chuan.gateway.memory_ops import MemoryOperations
from chuan.gateway.message_router import MessageRouter


# --------------------------------------------------------------------- #
# MessageRouter
# --------------------------------------------------------------------- #
def test_simplify_traditional_to_simplified() -> None:
    assert MessageRouter.simplify("武漢的天氣") == "武汉的天气"


def test_simplify_preserves_unmapped_and_empty() -> None:
    assert MessageRouter.simplify("hello 世界") == "hello 世界"
    assert MessageRouter.simplify("") == ""


def test_ground_weather_passthrough_when_not_weather() -> None:
    # 非天气意图应原样返回，且不触碰 supervisor（天气意图检测在前）
    router = MessageRouter(object())
    assert router.ground_weather("帮我写个 hello world") == "帮我写个 hello world"


# --------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------- #
class _Brain:
    def __init__(self, name: str = "cloud_general") -> None:
        self.name = name


class _Brains:
    def __init__(self, brain) -> None:
        self._brain = brain

    def default(self):
        return self._brain


class _MCP:
    def __init__(self, servers=None) -> None:
        self._servers = list(servers or [])

    def connected_servers(self) -> list[str]:
        return list(self._servers)


class _Memory:
    def __init__(self, checkpointer=None) -> None:
        self.checkpointer = checkpointer


class _Supervisor:
    def __init__(
        self,
        *,
        awake: bool = True,
        brain=None,
        mcp_servers: list[str] | None = None,
        checkpointer=None,
        workers: int = 2,
        consolidation: str | None = None,
    ) -> None:
        self.brains = _Brains(brain)
        self.mcp_adapter = _MCP(mcp_servers)
        self.memory = _Memory(checkpointer)
        self._is_awake = awake
        self._workers = {f"w{i}": object() for i in range(workers)}
        self.consolidation_status = consolidation


def test_heartbeat_check_healthy() -> None:
    sup = _Supervisor(
        brain=_Brain(), mcp_servers=["weather"], checkpointer="sqlite",
        workers=3, consolidation="巩固 2 会话",
    )
    report = Heartbeat(sup).check()
    assert report["healthy"] is True
    assert report["awake"] is True
    assert report["brain"] == "cloud_general"
    assert report["workers"] == 3
    assert report["mcp_connected"] == 1
    assert report["consolidation"] == "巩固 2 会话"


def test_heartbeat_check_degraded_without_brain() -> None:
    sup = _Supervisor(awake=True, brain=None)
    report = Heartbeat(sup).check()
    assert report["healthy"] is False
    assert report["brain_ok"] is False
    assert report["brain"] == ""


def test_heartbeat_summary() -> None:
    sup = _Supervisor(brain=_Brain(), mcp_servers=["weather"])
    summary = Heartbeat(sup).summary()
    assert "健康" in summary
    assert "成员 2" in summary
    assert "cloud_general" in summary


def test_heartbeat_summary_degraded() -> None:
    sup = _Supervisor(awake=True, brain=None)
    assert "降级" in Heartbeat(sup).summary()


class _NRole:
    """带 agent_count 的岗位替身（N37 1:N）。"""

    def __init__(self, n: int) -> None:
        self._n = n

    def agent_count(self) -> int:
        return self._n


def test_heartbeat_role_agents_aggregates_n() -> None:
    """N37：健康报告汇总各岗位持有的 agent 实例总数（1 → N）。"""
    sup = _Supervisor(brain=_Brain())
    sup._workers = {"w1": _NRole(1), "w2": _NRole(3)}
    sup._chief_role = _NRole(2)
    report = Heartbeat(sup).check()
    assert report["role_agents"] == 6  # 1 + 3 + 2
    assert "agent 6" in Heartbeat(sup).summary()


# --------------------------------------------------------------------- #
# MemoryOperations
# --------------------------------------------------------------------- #
class _MemoryOnlySupervisor:
    def __init__(self, memory) -> None:
        self.memory = memory


class _ReindexingMemory(_Memory):
    def __init__(self) -> None:
        super().__init__(None)
        self.reindex_calls = 0

    def reindex(self) -> None:
        self.reindex_calls += 1


def test_memory_ops_reindex_calls_memory() -> None:
    mem = _ReindexingMemory()
    MemoryOperations(_MemoryOnlySupervisor(mem)).reindex()
    assert mem.reindex_calls == 1


def test_memory_ops_reindex_noop_without_reindex() -> None:
    MemoryOperations(_MemoryOnlySupervisor(_Memory())).reindex()  # 不抛异常


# --------------------------------------------------------------------- #
# CronManager
# --------------------------------------------------------------------- #
class _RecordingScheduler:
    def __init__(self) -> None:
        self.added: list[tuple] = []
        self.started: dict | None = None

    def add_interval_job(self, name, message, **kwargs) -> None:
        self.added.append((name, message, kwargs))

    def start(self, **kwargs) -> None:
        self.started = kwargs


class _CronSupervisor:
    def __init__(self, config_path, scheduler) -> None:
        self.config_path = config_path
        self.scheduler = scheduler


def test_cron_loads_enabled_jobs(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "scheduler:\n"
        "  enabled: true\n"
        "  poll_interval_seconds: 2.5\n"
        "  jobs:\n"
        "    - name: morning\n"
        "      message: 日报\n"
        "      interval_seconds: 3600\n"
        "      agent: housekeeper\n"
        "      run_immediately: true\n",
        encoding="utf-8",
    )
    scheduler = _RecordingScheduler()
    CronManager(_CronSupervisor(config, scheduler)).load_scheduled_jobs()
    assert scheduler.added == [("morning", "日报", {
        "interval_seconds": 3600.0,
        "agent_name": "housekeeper",
        "run_immediately": True,
    })]
    assert scheduler.started == {"poll_interval_seconds": 2.5}


def test_cron_skips_when_disabled(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("scheduler:\n  enabled: false\n", encoding="utf-8")
    scheduler = _RecordingScheduler()
    CronManager(_CronSupervisor(config, scheduler)).load_scheduled_jobs()
    assert scheduler.added == []
    assert scheduler.started is None