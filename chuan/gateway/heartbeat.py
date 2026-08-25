"""⑥ Heartbeat —— 健康检查与角色状态监控。

职责：产出幕僚长运行时健康报告，供状态栏/告警使用。
此为 Gateway 七大组件中唯一未在旧 codebase 实现的组件（全新）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor


class Heartbeat:
    """健康检查：汇总幕僚长、大脑、MCP、岗位、记忆的运行时状态。"""

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    def check(self) -> dict[str, Any]:
        """返回健康报告字典；不含昂贵调用（不触发 LLM）。"""
        sup = self._sup
        brain_name = ""
        brain_ok = False
        try:
            brain = sup.brains.default()
            brain_ok = brain is not None
            brain_name = getattr(brain, "name", "") or ""
        except Exception:  # noqa: BLE001 - 大脑解析失败视为降级
            brain_ok = False

        try:
            mcp_connected = len(sup.mcp_adapter.connected_servers())
        except Exception:  # noqa: BLE001
            mcp_connected = 0

        memory_ready = getattr(sup.memory, "checkpointer", None) is not None

        # N37 岗位化 1:N：各岗位持有的 agent 实例总数（默认 + 扩容）
        try:
            roles = [*sup._workers.values(), getattr(sup, "_chief_role", None)]
            total_agents = sum(
                getattr(role, "agent_count", lambda: 0)()
                for role in roles
                if role is not None
            )
        except Exception:  # noqa: BLE001 - 计数失败不影响健康报告
            total_agents = 0

        # N41 动态实例池：各岗位池扩缩容状态（容量/空闲/用量），供 TUI/心跳观测
        try:
            pools: list[dict] = []
            pool_total = {"size": 0, "idle": 0, "min": 0, "max": 0}
            for role in roles:
                if role is None:
                    continue
                stats = role.pool_stats()
                if not stats or stats.get("min") is None:
                    continue  # 未开启动态池的岗位不计入
                pools.append({"role": getattr(role, "name", ""), **stats})
                pool_total["size"] += stats.get("size", 0)
                pool_total["idle"] += stats.get("idle", 0)
                pool_total["min"] += stats.get("min", 0) or 0
                pool_total["max"] += stats.get("max", 0) or 0
        except Exception:  # noqa: BLE001 - 池统计失败不影响健康报告
            pools, pool_total = [], {"size": 0, "idle": 0, "min": 0, "max": 0}

        report: dict[str, Any] = {
            "awake": sup._is_awake,
            "brain": brain_name,
            "brain_ok": brain_ok,
            "workers": len(sup._workers),
            "role_agents": total_agents,
            "pools": pools,
            "pool_total": pool_total,
            "mcp_connected": mcp_connected,
            "memory_ready": memory_ready,
            "consolidation": sup.consolidation_status,
        }

        # N45 事件总线 + 任务队列状态（旁路，失败按空）
        try:
            report["bus"] = sup.bus.stats()
        except Exception:  # noqa: BLE001
            report["bus"] = {}
        try:
            report["queue"] = sup.task_queue.stats()
        except Exception:  # noqa: BLE001
            report["queue"] = {}

        # N51 工具市场状态（开启时报告上下架数量；关闭/失败按空）
        try:
            report["market"] = sup.tool_market.stats()
        except Exception:  # noqa: BLE001
            report["market"] = {}

        report["healthy"] = sup._is_awake and brain_ok
        return report

    def summary(self) -> str:
        """一行健康摘要，供 TUI 状态栏等前端展示。"""
        r = self.check()
        parts = [f"成员 {r['workers']}", f"agent {r['role_agents']}",
                 f"脑 {r['brain'] or '缺失'}", f"MCP {r['mcp_connected']}"]
        pt = r.get("pool_total")
        if pt and pt.get("max"):
            parts.append(f"池 {pt['size']}/{pt['max']}")
        # N45：总线/队列后端（redis / memory / off）
        bus_backend = (r.get("bus") or {}).get("backend", "")
        if bus_backend and bus_backend != "disabled":
            parts.append(f"总线 {bus_backend}")
        status = "健康" if r["healthy"] else "降级"
        return f"{status} · " + " · ".join(parts)