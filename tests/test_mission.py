"""N32 Mission 长任务追踪：CRUD/持久化/状态机/看板接口/harness 关联。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from chuan.gateway.agent_harness import AgentHarness
from chuan.memory import Memory
from chuan.mission import MissionManager
from chuan.runtime_supervisor import RuntimeSupervisor


def _memory(tmp_path: Path) -> Memory:
    return Memory(vault_path=tmp_path / "vault")


def _mgr(tmp_path: Path, data_root: Path | None = None) -> MissionManager:
    return MissionManager(
        _memory(tmp_path), data_root=data_root or (tmp_path / "missions.json")
    )


# --------------------------------------------------------------------- #
# MissionManager CRUD + 持久化
# --------------------------------------------------------------------- #
def test_mission_start_get_list(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    m = mgr.start("m1", "重构登录模块", agent="claude_code")
    assert m.status == "active"
    assert mgr.get("m1") is not None
    assert [x.name for x in mgr.list()] == ["m1"]
    assert mgr.list(status="done") == []


def test_mission_persist_reload(tmp_path: Path) -> None:
    data_root = tmp_path / "missions.json"
    mgr = _mgr(tmp_path, data_root)
    mgr.start("m1", "目标一")
    mgr.update("m1", progress="进行中", status="paused", task_id="delegate-1")

    mgr2 = _mgr(tmp_path, data_root)
    m = mgr2.get("m1")
    assert m is not None and m.status == "paused"
    assert m.progress == "进行中"
    assert m.task_ids == ["delegate-1"]


def test_mission_start_validates(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    for name, goal in (("", "目标"), ("m", ""), ("  ", "目标")):
        try:
            mgr.start(name, goal)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝空 name/goal: {name!r} {goal!r}")


def test_mission_update_progress_status_task(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.start("m1", "目标")
    assert mgr.update("不存在", progress="x") is False
    assert mgr.update("m1", progress="P1", task_id="delegate-1") is True
    assert mgr.update("m1", progress="P2", task_id="delegate-1") is True  # task 去重
    m = mgr.get("m1")
    assert m.progress == "P2" and m.task_ids == ["delegate-1"]
    assert mgr.update("m1", status="bad") is True  # 非法状态忽略
    assert m.status == "active"


def test_mission_finish_pause_resume_remove(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.start("m1", "目标")
    assert mgr.finish("m1", "完成", success=True) is True
    assert mgr.get("m1").status == "done"
    assert mgr.finish("m1", "失败", success=False) is True
    assert mgr.get("m1").status == "failed"
    assert mgr.pause("m1") is True and mgr.get("m1").status == "paused"
    assert mgr.resume("m1") is True and mgr.get("m1").status == "active"
    assert mgr.remove("m1") is True
    assert mgr.remove("m1") is False  # 幂等
    assert mgr.list() == []


# --------------------------------------------------------------------- #
# Harness 关联：submit 透传 mission
# --------------------------------------------------------------------- #
def test_harness_submit_carries_mission() -> None:
    harness = AgentHarness(SimpleNamespace())
    # 预置一个 pending 依赖任务，避免新任务被调度执行
    harness._tasks["base"] = {
        "task_id": "base", "status": "pending", "depends_on": [],
        "success": None, "result": "", "on_done": None, "loop": None,
        "mission": "",
    }
    tid = harness.submit("claude_code", "任务", depends_on=["base"], mission="my_mission")
    entry = harness.get(tid)
    assert entry["status"] == "pending"  # 依赖未结束 → 未执行
    assert entry["mission"] == "my_mission"
    # 未指定 mission 默认空串
    tid2 = harness.submit("claude_code", "任务", depends_on=["base"])
    assert harness.get(tid2)["mission"] == ""


# --------------------------------------------------------------------- #
# 幕僚长：后台任务完成自动回写 mission 进度
# --------------------------------------------------------------------- #
def test_on_harness_done_updates_mission(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    missions = MissionManager(memory, data_root=tmp_path / "missions.json")
    missions.start("m1", "目标")
    sup = SimpleNamespace(missions=missions)

    RuntimeSupervisor._on_harness_done(sup, {
        "mission": "m1", "success": True, "task_id": "delegate-1", "result": "搞定",
    })
    m = missions.get("m1")
    assert m.status == "active"  # 不自动终结
    assert "delegate-1" in m.task_ids
    assert "[完成]" in m.progress and "搞定" in m.progress

    # 失败任务也回写
    RuntimeSupervisor._on_harness_done(sup, {
        "mission": "m1", "success": False, "task_id": "delegate-2", "result": "报错",
    })
    m = missions.get("m1")
    assert "[失败]" in m.progress and "delegate-2" in m.task_ids

    # 无 mission 关联的任务不动任何 mission
    RuntimeSupervisor._on_harness_done(sup, {"success": True, "task_id": "delegate-3"})
    assert m.task_ids == ["delegate-1", "delegate-2"]


# --------------------------------------------------------------------- #
# 幕僚长管理接口（最小替身）
# --------------------------------------------------------------------- #
class _SupLike(RuntimeSupervisor):
    def __init__(self, memory: Memory, data_root: Path) -> None:
        self.memory = memory
        self.missions = MissionManager(memory, data_root=data_root)


def test_supervisor_mission_interface(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    sup = _SupLike(memory, tmp_path / "missions.json")

    assert "已登记" in sup.mission_start("m1", "重构登录模块")
    assert "添加失败" in sup.mission_start("m1", "")  # 空目标报可读错误
    items = sup.mission_list()
    assert len(items) == 1 and items[0]["tasks"] == 0

    assert "已标记" in sup.mission_finish("m1", "完成", success=True)
    assert sup.mission_list()[0]["status"] == "done"
    assert "已暂停" in sup.mission_pause("m1")
    assert "已恢复" in sup.mission_resume("m1")
    assert "已删除" in sup.mission_remove("m1")
    assert "未找到" in sup.mission_remove("m1")
    assert "未找到" in sup.mission_finish("不存在", "x")
