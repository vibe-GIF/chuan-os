"""N17 TUI 测试 —— 主题、桥、App 冒烟。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from rich.text import Text

from chuan.tui.bridge import SupervisorBridge
from chuan.tui.theme import (
    BLUE, VIOLET, chuan_mark, gradient_text, role_color, waterline_frames,
)

# --------------------------------------------------------------------- #
# theme
# --------------------------------------------------------------------- #


def test_gradient_text_per_char_spans() -> None:
    text = gradient_text("chuan-os")
    assert text.plain == "chuan-os"
    # 每个字符一个 span（逐字符插值）
    assert len(text.spans) == 8
    # 首尾颜色分别接近蓝/紫
    first = str(text.spans[0].style)
    last = str(text.spans[-1].style)
    assert first.lower().startswith("#4a9e")
    assert last.lower().startswith("#a78b")


def test_chuan_mark_style() -> None:
    """川字标应使用加粗和川蓝样式。"""
    mark = chuan_mark()
    assert mark.plain == "川"
    style = str(mark.style)
    assert "bold" in style
    assert BLUE in style


def test_waterline_frames_growth() -> None:
    frames = waterline_frames(rows=7, steps=8)
    assert len(frames) == 8
    # 首帧 3 个水块（每列最少 1），末帧 3+7+5=15 个水块
    assert frames[0].plain.count("█") == 3
    assert frames[-1].plain.count("█") == 15
    # 7 行
    assert frames[-1].plain.count("\n") == 6
    # 每行长度一致（无尾随空格，居中不偏移）
    lines = frames[-1].plain.split("\n")
    assert len(set(len(ln) for ln in lines)) == 1


def test_role_color_mapping_and_fallback() -> None:
    assert role_color("housekeeper") == BLUE
    assert role_color(display="研究") == VIOLET
    # 未知角色：确定性兜底（同名字永远同色）
    assert role_color("unknown_role") == role_color("unknown_role")
    assert role_color("unknown_role") != role_color("another_unknown")


# --------------------------------------------------------------------- #
# bridge（用 Fake supervisor，不启动真实幕僚长）
# --------------------------------------------------------------------- #


class FakeHarness:
    """最小 AgentHarness 替身：记录 on_done 回调，可手动触发完成事件。"""

    def __init__(self) -> None:
        self.cbs: list = []

    def on_done(self, cb) -> None:
        self.cbs.append(cb)

    def fire(self, info: dict) -> None:
        for cb in self.cbs:
            cb(info)


class FakeSupervisor:
    def __init__(self, on_progress=None, on_proactive_alert=None) -> None:
        self._on_progress = on_progress
        self._on_alert = on_proactive_alert
        self.awake = False
        self.dispatch_calls: list[str] = []
        self.agent_harness = FakeHarness()

    def wake_up(self) -> None:
        time.sleep(0.02)
        self.awake = True

    def route_preview(self, message: str) -> str | None:
        return "housekeeper" if "天气" in message else None

    def dispatch(self, message, history=None, session_id="default"):
        self.dispatch_calls.append(message)
        if self._on_progress:
            self._on_progress({"event": "subtask_start", "role": "管家",
                               "subtask": 1, "description": "demo"})
        return {"messages": [{"role": "assistant", "content": f"echo:{message}"}],
                "route": "housekeeper", "route_method": "keyword"}

    def dispatch_to(self, worker_name, message, session_id="default"):
        return {"messages": [{"role": "assistant",
                              "content": f"{worker_name}:{message}"}]}

    def list_workers(self) -> list[str]:
        return ["housekeeper", "researcher"]

    def get_worker(self, name: str):
        displays = {"housekeeper": "管家", "researcher": "研究"}
        return SimpleNamespace(display_name=displays.get(name, name))

    def brains(self):  # pragma: no cover - 不在 fake 上测
        return self

    def delegate(self, agent_name: str, task: str, mission: str = "") -> str:
        return "delegate-fake-1"

    def delegate_snapshot(self) -> list[dict]:
        return [{
            "task_id": "delegate-1", "agent": "claude_code", "task": "写个脚本",
            "status": "running", "success": None, "result": "",
            "created_at": "2026-08-23T00:00:00+00:00", "finished_at": None,
        }]

    def mcp_status(self) -> list[dict]:
        return [
            {"name": "filesystem", "configured": True, "connected": True,
             "tools": 8, "command": "python", "args": [], "description": "文件系统 MCP",
             "error": ""},
            {"name": "opencode", "configured": True, "connected": False,
             "tools": 0, "command": "python", "args": [], "description": "",
             "error": "启动失败"},
        ]

    def mcp_connect(self, name: str) -> bool:
        return True

    def mcp_disconnect(self, name: str) -> bool:
        return True

    def howto_staging(self) -> list[dict]:
        return [{
            "name": "部署周报", "trigger": "每周五 汇总部署 周报",
            "process": "1. 汇总变更\n2. 生成周报", "tools": ["bash"],
            "source": "role:operator", "task": "帮我部署周报，周五要发",
            "created": "2026-08-24T10:00:00",
        }]

    def howto_show(self, name: str) -> str:
        return (f"候选：{name}（来源 role:operator）\n"
                f"触发：每周五 汇总部署 周报\n怎么做：\n1. 汇总变更\n2. 生成周报")

    def howto_approve(self, name: str, rename=None) -> str:
        return f"已入库 {name}.md"

    def howto_discard(self, name: str) -> str:
        return f"已丢弃候选：{name}"

    def routine_list(self) -> list[dict]:
        return [{
            "name": "deploy_report", "message": "帮我生成部署周报",
            "schedule": "fri 17:30", "agent": "housekeeper",
            "archive_to_wiki": True, "next_run": "08-28 17:30",
        }]

    def routine_add(self, name: str, message: str, schedule: str,
                    archive_to_wiki: bool = False, retries: int = 0) -> str:
        return f"已添加例行任务「{name}」({schedule})"

    def routine_remove(self, name: str) -> str:
        return f"已移除例行任务：{name}"

    def aci_status(self) -> dict:
        return {"memory": 2, "wiki": 1, "total": 3, "injected": True}

    def resume_list(self) -> list[dict]:
        return [{
            "session_id": "default", "role": "研究", "task": "调研方案",
            "total": 3, "done": 1,
            "updated_at": "2026-08-24T12:00:00",
        }]

    def resume_to(self, worker_name: str, session_id: str) -> str:
        return f"[续跑完成] 已复用已完成子任务，跑完剩余部分"

    def resume_clear(self, session_id: str) -> str:
        return f"已清除断点档案：{session_id}"

    def shutdown(self) -> None:
        self.awake = False


def _make_bridge() -> SupervisorBridge:
    return SupervisorBridge(
        supervisor_factory=lambda **kwargs: FakeSupervisor(**kwargs))


def test_bridge_start_and_ready() -> None:
    bridge = _make_bridge()
    bridge.start()
    assert bridge.wait_ready(timeout=5)
    assert bridge.ready
    assert bridge.error is None


def test_bridge_workers_and_preview() -> None:
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    workers = bridge.workers()
    assert ("housekeeper", "管家") in workers
    assert ("researcher", "研究") in workers
    assert bridge.route_preview("武汉天气") == "housekeeper"
    assert bridge.route_preview("随便聊聊") is None


def test_bridge_send_and_progress_events() -> None:
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    result = asyncio.run(bridge.send("武汉天气"))
    assert result["route"] == "housekeeper"
    assert result["messages"][0]["content"] == "echo:武汉天气"
    # dispatch 触发的进度事件应排进队列
    events = bridge.drain_events()
    assert any(e["event"] == "subtask_start" for e in events)


def test_bridge_send_to_locked_role() -> None:
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    result = asyncio.run(bridge.send_to("researcher", "调研一下"))
    assert result["messages"][0]["content"] == "researcher:调研一下"


def test_bridge_send_before_ready_raises() -> None:
    bridge = _make_bridge()  # 未 start
    with pytest.raises(RuntimeError):
        asyncio.run(bridge.send("hi"))


def test_bridge_memory_note_count() -> None:
    """长期笔记计数：有 vault 时数 notes/*.md，无 memory 属性按 0。"""
    import tempfile
    from pathlib import Path

    class FakeWithVault(FakeSupervisor):
        @property
        def memory(self):  # type: ignore[override]
            return SimpleNamespace(vault_path=Path(self._vault))

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "notes").mkdir()
        (vault / "notes" / "a.md").write_text("x", encoding="utf-8")
        (vault / "notes" / "sub").mkdir()
        (vault / "notes" / "sub" / "b.md").write_text("y", encoding="utf-8")

        fake = FakeWithVault()
        fake._vault = tmp
        bridge = SupervisorBridge(supervisor_factory=lambda **k: fake)
        bridge.start()
        bridge.wait_ready(timeout=5)
        assert bridge.memory_note_count() == 2

    # FakeSupervisor 无 memory 属性 → 0，不抛异常
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    assert bridge.memory_note_count() == 0


# --------------------------------------------------------------------- #
# App 冒烟（textual run_test 无头模式；textual 缺失时跳过）
# --------------------------------------------------------------------- #


def _ready_bridge() -> SupervisorBridge:
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    return bridge


async def test_app_turn_flow() -> None:
    """提交消息 → 路由行 → 助手回复 → 可折叠路由树。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        # 等输入框可用（splash 未关时禁用）
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        # 任意键关掉 splash，聚焦输入框
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "武汉天气"
        await pilot.press("enter")

        # 等回合完成
        for _ in range(100):
            await pilot.pause(0.05)
            if not app._busy:
                break
        assert not app._busy

        chat_text = ""
        for w in app.query("Static"):
            chat_text += str(w.render())
        assert "echo:武汉天气" in chat_text        # 助手回复
        assert "housekeeper" in chat_text or "管家" in chat_text  # 路由行
        # mockup 路由树语义：├ 路由 幕僚长 → 管家 · 关键词
        assert "├ 路由 幕僚长 → 管家" in chat_text
        assert "关键词" in chat_text
        assert "你>" in chat_text                  # 用户行前缀


async def test_app_splash_dismiss_and_command() -> None:
    """splash 任意键消失；/help 输出系统行。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        assert app.query_one("#splash").display
        await pilot.press("enter")   # 关 splash
        await pilot.pause(0.05)
        assert not app.query_one("#splash").display

        inp = app.query_one("#input")
        inp.value = "/help"
        await pilot.press("enter")
        await pilot.pause(0.1)
        chat_text = ""
        for w in app.query("Static"):
            chat_text += str(w.render())
        assert "/help" in chat_text
        assert "后台委派" in chat_text or "/bg" in chat_text


# --------------------------------------------------------------------- #
# 后台委派：bridge delegate + TUI 看板渲染
# --------------------------------------------------------------------- #


def test_bridge_delegate_and_snapshot() -> None:
    """bridge.delegate() 转发 + delegate_snapshot() 取快照。"""
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    assert bridge.delegate("claude_code", "写个脚本") == "delegate-fake-1"
    tasks = bridge.delegate_snapshot()
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "delegate-1"
    assert tasks[0]["agent"] == "claude_code"


async def test_app_bg_command_and_tasks_board() -> None:
    """/bg 派发立即回报；/tasks 把看板打进对话区。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/bg claude_code 写个脚本"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "已派发 claude_code" in text

        inp.value = "/tasks"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "后台任务 · 1 条" in text
        assert "delegate-1" in text
        assert "claude_code" in text


async def test_app_delegate_done_event_renders() -> None:
    """后台任务完成事件 → 对话区显示结果摘要。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    fake = FakeSupervisor()
    bridge = SupervisorBridge(supervisor_factory=lambda **k: fake)
    bridge.start()
    bridge.wait_ready(timeout=5)
    assert fake.agent_harness.cbs  # bridge 已注册 on_done

    app = app_mod.ChuanTUI(bridge=bridge)
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        fake.agent_harness.fire({
            "event": "delegate_done", "task_id": "delegate-1",
            "agent": "claude_code", "task": "写个脚本",
            "status": "done", "success": True, "result": "ECHO:写个脚本",
        })
        for _ in range(20):
            await pilot.pause(0.05)
            text = "".join(str(w.render()) for w in app.query("Static"))
            if "后台任务 · claude_code" in text:
                break
        assert "后台任务 · claude_code" in text
        assert "完成" in text
        assert "ECHO:写个脚本" in text


# --------------------------------------------------------------------- #
# MCP 管理面板：bridge 转发 + /mcp 渲染
# --------------------------------------------------------------------- #


def test_bridge_mcp_status_and_actions() -> None:
    """bridge 转发 mcp_status / mcp_connect / mcp_disconnect。"""
    bridge = _make_bridge()
    bridge.start()
    bridge.wait_ready(timeout=5)
    status = bridge.mcp_status()
    assert len(status) == 2
    assert status[0]["name"] == "filesystem"
    assert status[0]["connected"] is True
    assert status[1]["name"] == "opencode"
    assert status[1]["connected"] is False
    assert bridge.mcp_connect("filesystem") is True
    assert bridge.mcp_disconnect("opencode") is True


async def test_app_mcp_panel_renders() -> None:
    """/mcp 面板列出每个 server 的连接/工具/错误状态。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/mcp"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "MCP Servers · 2 个已配置" in text
        assert "filesystem" in text
        assert "opencode" in text
        assert "工具 8" in text  # 已连接显示工具数


async def test_app_mcp_toggle_renders_panel() -> None:
    """/mcp off <name> 触发开关并重绘面板。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/mcp off opencode"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "/mcp off opencode" in text
        assert "MCP Servers · 2 个已配置" in text


# --------------------------------------------------------------------- #
# N27 知识原子自动沉淀：bridge 转发 + /howto 队列面板
# --------------------------------------------------------------------- #
def test_bridge_howto_status_and_actions() -> None:
    """bridge 转发 howto_staging / show / approve / discard。"""
    bridge = _ready_bridge()
    pending = bridge.howto_staging()
    assert len(pending) == 1 and pending[0]["name"] == "部署周报"
    assert "每周五" in bridge.howto_show("部署周报")
    assert "已入库" in bridge.howto_approve("部署周报")
    assert "已丢弃" in bridge.howto_discard("部署周报")


async def test_app_howto_panel_renders() -> None:
    """/howto 面板列出待确认知识原子。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/howto"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "HowTo · 待确认 1 条" in text
        assert "部署周报" in text
        assert "/howto show" in text


async def test_app_howto_approve_renders_panel() -> None:
    """/howto approve <name> 触发确认并重绘面板。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/howto approve 部署周报"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "已入库 部署周报.md" in text
        assert "HowTo · 待确认 1 条" in text  # approve 后重绘面板


# --------------------------------------------------------------------- #
# N28 例行自动化闭环：bridge 转发 + /routine 面板
# --------------------------------------------------------------------- #
def test_bridge_routine_status_and_actions() -> None:
    """bridge 转发 routine_list / add / remove。"""
    bridge = _ready_bridge()
    items = bridge.routine_list()
    assert len(items) == 1 and items[0]["name"] == "deploy_report"
    assert "已添加" in bridge.routine_add("x", "任务", "fri@17:30")
    assert "已移除" in bridge.routine_remove("deploy_report")


async def test_app_routine_panel_renders() -> None:
    """/routine 面板列出例行任务（自转闭环看板）。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/routine"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "Routine · 1 条例行任务" in text
        assert "deploy_report" in text
        assert "fri 17:30" in text


# --------------------------------------------------------------------- #
# N33 ACI 预判注入：bridge 转发 + /aci 面板
# --------------------------------------------------------------------- #
def test_bridge_aci_status() -> None:
    """bridge 转发 aci_status。"""
    bridge = _ready_bridge()
    st = bridge.aci_status()
    assert st["memory"] == 2 and st["wiki"] == 1 and st["injected"] is True


async def test_app_aci_panel_renders() -> None:
    """/aci 面板展示最近一次预取统计（预判注入看板）。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/aci"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "ACI 预判注入" in text
        assert "记忆 2" in text and "wiki 1" in text
        assert "已注入" in text


# --------------------------------------------------------------------- #
# N35 断点续跑：bridge 转发 + /resume 面板
# --------------------------------------------------------------------- #
def test_bridge_resume_status_and_actions() -> None:
    """bridge 转发 resume_list / resume_to / resume_clear。"""
    bridge = _ready_bridge()
    items = bridge.resume_list()
    assert len(items) == 1 and items[0]["session_id"] == "default"
    assert "续跑完成" in bridge.resume_to("researcher", "default")
    assert "已清除" in bridge.resume_clear("default")


async def test_app_resume_panel_renders() -> None:
    """/resume 面板列出可恢复断点（打断不丢工具看板）。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "/resume"
        await pilot.press("enter")
        await pilot.pause(0.1)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "Resume · 1 条可恢复断点" in text
        assert "default" in text and "1/3" in text
        assert "调研方案" in text


# --------------------------------------------------------------------- #
# 路由树事件格式化 / 命令面板 / Esc 软中断
# --------------------------------------------------------------------- #


def test_format_event_tool_and_memory() -> None:
    """工具调用/记忆召回/规划等进度事件 → 路由树行。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    fmt = app_mod._format_event
    # 普通工具调用
    assert "bash" in fmt({"event": "tool_call", "tool": "bash", "input": "ls -la"})
    # 记忆召回 / 记忆写入专用文案
    assert "记忆召回" in fmt(
        {"event": "tool_call", "tool": "recall_memory", "input": "天气"})
    assert "记忆写入" in fmt(
        {"event": "tool_call", "tool": "remember_memory", "input": "x"})
    # 工具完成：成功 ✓ / 失败 ✕
    assert "✓" in fmt({"event": "tool_done", "tool": "bash", "output": "ok"})
    assert "✕" in fmt({"event": "tool_done", "tool": "bash", "error": "boom"})
    # 规划 / 子任务 / 完成
    assert "plan" in fmt({"event": "plan", "count": 3, "waves": 2})
    assert "subtask" in fmt(
        {"event": "subtask_start", "subtask": "s1", "description": "查数据"})
    assert "done" in fmt({"event": "done"})


async def test_command_palette_open_execute_and_close() -> None:
    """Ctrl+P 打开命令面板 → 字母键执行 → Esc 关闭。"""
    app_mod = pytest.importorskip("chuan.tui.app")
    app = app_mod.ChuanTUI(bridge=_ready_bridge())
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")   # 关 splash
        await pilot.pause(0.05)

        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        assert isinstance(app.screen, app_mod.CommandPalette)

        # 执行 "h" → /help，面板关闭并输出系统行
        await pilot.press("h")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, app_mod.CommandPalette)
        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "/help" in text

        # 再开一次，Esc 应关闭而不执行任何动作
        await pilot.press("ctrl+p")
        await pilot.pause(0.1)
        assert isinstance(app.screen, app_mod.CommandPalette)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, app_mod.CommandPalette)


async def test_esc_soft_interrupt() -> None:
    """慢 dispatch 下按 Esc 中止当前回合，输出中断提示。"""
    app_mod = pytest.importorskip("chuan.tui.app")

    class SlowSupervisor(FakeSupervisor):
        def dispatch(self, message, history=None, session_id="default"):
            time.sleep(2)  # 足够慢，保证 Esc 在忙时按下
            return {"messages": [{"role": "assistant", "content": "late"}]}

    bridge = SupervisorBridge(supervisor_factory=lambda **k: SlowSupervisor(**k))
    bridge.start()
    bridge.wait_ready(timeout=5)

    app = app_mod.ChuanTUI(bridge=bridge)
    async with app.run_test() as pilot:
        for _ in range(50):
            if not app.query_one("#input").disabled:
                break
            await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.05)

        inp = app.query_one("#input")
        inp.value = "慢任务"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app._busy

        await pilot.press("escape")
        for _ in range(30):
            await pilot.pause(0.05)
            if not app._busy:
                break
        assert not app._busy

        text = "".join(str(w.render()) for w in app.query("Static"))
        assert "中断" in text
