"""N17 TUI 主程序 —— chuan-os 调试终端。

布局（对齐 mockup 定稿）：
    顶栏：川标 + 品牌渐变 + 会话 + 路由模式（自动/锁定）+ 脑档位
    左侧班底面板：全部角色（ASCII 小像 + 专属色），点击锁定 / auto-route
    对话区：消息流 + 路由树（路由判定 → 工具调用 → 事件，可折叠）
    输入框 / 状态栏：系统状态 · 脑 · 记忆条数 · 班底 · uptime

门面（splash）：水线汇聚动画（逐行居中）→ chuan-os wordmark → 任意键进入主屏。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from rich.console import Group
from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Input, Static

from chuan.tui.bridge import SupervisorBridge
from chuan.tui.theme import (
    BG, BLUE, BORDER, DEFAULT_COLOR, DIM, GREEN, MID, MUTED, SURFACE, TEXT, VIOLET,
    chuan_mark, gradient_text, role_avatar, role_color, waterline_frames,
)

_SESSION = "default"
_VERSION = "v0.1.0"
_SPLASH_FRAMES = 24  # 每帧 60ms ≈ 1.4s

# 路由方式 → 中文标签（mockup 路由树语义）
_METHOD_LABELS = {
    "keyword": "关键词", "llm": "LLM", "chief": "幕僚长", "locked": "锁定",
    "howto_confirm": "知识原子确认",
}


def _last_message(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "(no content)"
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    return str(content or "(no content)")


def _format_event(ev: dict[str, Any]) -> str:
    """岗位进度事件 → 路由树行（含工具调用/记忆召回）。"""
    kind = ev.get("event")
    if kind == "tool_call":
        tool = ev.get("tool", "tool")
        if tool in ("recall_memory",):
            label = "记忆召回"
        elif tool in ("remember_memory", "append_role_memory"):
            label = "记忆写入"
        else:
            label = tool
        inp = str(ev.get("input", "")).strip()
        detail = _shorten(inp, 28)
        return f"[{VIOLET}]├ ⎿ {escape(label)}[/] [{MUTED}]{escape(detail)}[/]"
    if kind == "tool_done":
        if ev.get("error"):
            return f"[{VIOLET}]├ ⎿[/] [red]✕ {escape(str(ev.get('error')))}[/]"
        out = str(ev.get("output", "")).strip()
        detail = _shorten(out, 28)
        return f"[{GREEN}]├ ⎿ ✓[/] [{MUTED}]{escape(detail)}[/]"
    if kind == "plan":
        return f"[{MUTED}]├ plan: {ev.get('count')} subtasks · {ev.get('waves')} waves[/]"
    if kind == "subtask_start":
        return f"[{MUTED}]├ subtask {ev.get('subtask')}: {escape(str(ev.get('description', '')))}[/]"
    if kind == "subtask_retry":
        return f"[{MUTED}]├ subtask {ev.get('subtask')} retry #{ev.get('attempt')}[/]"
    if kind == "subtask_done":
        mark, style = ("✓", GREEN) if ev.get("success") else ("✕", "red")
        return f"[{style}]├ subtask {ev.get('subtask')} {mark}[/]"
    if kind == "done":
        return f"[{MUTED}]└ done[/]"
    return f"[{MUTED}]├ {escape(str(ev))}[/]"


def _shorten(text: str, limit: int) -> str:
    """单行截断，去掉换行/多余空白，超过 limit 加 …。"""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RoleTab(Static):
    """左侧角色面板里的一个可点击行。"""

    def __init__(self, name: str | None, label: str, color: str,
                 avatar: str, selected: bool = False) -> None:
        super().__init__(classes=f"roletab{' sel' if selected else ''}")
        self.role_name = name
        self._label = label
        self._color = color
        self._avatar = avatar
        self._selected = selected
        self._render_label()

    def _render_label(self) -> None:
        mark = "▸" if self._selected else " "
        self.update(f"[{self._color}]{mark} {self._avatar} {escape(self._label)}[/]")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.set_classes(f"roletab{' sel' if selected else ''}")
        self._render_label()

    def on_click(self) -> None:
        app = self.app
        if isinstance(app, ChuanTUI):
            app.select_role(self.role_name)


class CommandPalette(ModalScreen[None]):
    """Ctrl+P 命令面板：常用动作列表，回车执行，Esc 关闭。"""

    def __init__(self, commands: list[tuple[str, str, Any]]) -> None:
        super().__init__()
        self._commands = commands  # [(label, key, callable), …]

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Static("命令面板  ·  回车执行  ·  Esc 关闭", id="palette_title")
            for label, key, _ in self._commands:
                yield Static(f"[{BLUE}]{key}[/]  {escape(label)}", classes="pal_item")

    def on_key(self, event: events.Key) -> None:
        # 命中命令或 Esc 都吞掉事件，避免冒泡到主 App 的 on_key 触发软中断
        for _, key, fn in self._commands:
            if event.key == key:
                event.stop()
                event.prevent_default()
                fn()
                self.dismiss()
                return
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.dismiss()


class ChuanTUI(App[None]):
    """chuan-os 调试终端。"""

    TITLE = "chuan-os"
    CSS = f"""
    Screen {{
        background: {BG};
        layers: main splash;
    }}
    #main {{
        layer: main;
        layout: vertical;
        height: 100%;
    }}
    #topbar {{
        height: 1;
        background: {SURFACE};
        color: {MUTED};
        padding: 0 1;
        border-bottom: solid {BORDER};
    }}
    #body {{
        height: 1fr;
    }}
    #rolepanel {{
        width: 22;
        background: {SURFACE};
        border-right: solid {BORDER};
        padding: 1 0;
    }}
    #rolepanel_title {{
        height: 1;
        color: {MUTED};
        padding: 0 2;
        margin: 0 0 1 0;
    }}
    #right {{
        width: 1fr;
        layout: vertical;
    }}
    #chat {{
        padding: 0 1;
    }}
    #input {{
        border: round {BORDER};
        background: {BG};
        margin: 0 1;
    }}
    #input:focus {{
        border: round {BLUE};
    }}
    #status {{
        height: 1;
        background: #090d16;
        color: {DIM};
        padding: 0 1;
    }}
    .roletab {{
        height: 1;
        padding: 0 2;
        color: {MUTED};
    }}
    .roletab:hover {{
        background: #14213f;
    }}
    .roletab.sel {{
        background: #14213f;
    }}
    #splash {{
        layer: splash;
        background: {BG};
        width: 100%;
        height: 100%;
        align: center middle;
    }}
    #splash_content {{
        width: 100%;
        content-align-horizontal: center;
    }}
    """

    BINDINGS = [
        Binding("tab", "cycle_role", "Cycle role", priority=True),
        Binding("ctrl+l", "show_splash", "Splash", show=False),
        Binding("ctrl+p", "command_palette", "Commands", show=False),
    ]

    def __init__(self, bridge: SupervisorBridge | None = None,
                 session_id: str = _SESSION) -> None:
        super().__init__()
        self.bridge = bridge or SupervisorBridge()
        self.session_id = session_id
        self._busy = False
        self._turn: dict[str, Any] | None = None
        self._turn_task: asyncio.Task | None = None
        self._locked: str | None = None   # None = auto-route
        self._cycle_idx = 0
        self._roster: dict[str, str] = {}   # name → display
        self._mem_count = 0                 # 长期笔记数（缓存，回合结束刷新）
        self._splash_dismissed = False
        self._splash_frames: list[Text] = []
        self._splash_idx = 0
        self._start_time = time.monotonic()

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        with Static(id="main"):
            yield Static(id="topbar")
            with Horizontal(id="body"):
                with VerticalScroll(id="rolepanel"):
                    yield Static("角色班底 · 0", id="rolepanel_title")
                with Vertical(id="right"):
                    yield VerticalScroll(id="chat")
                    yield Input(id="input", placeholder="输入消息…  /help 查看命令")
                    yield Static(id="status")
        with Center(id="splash"):
            yield Static(id="splash_content")

    def on_mount(self) -> None:
        self._splash_frames = waterline_frames(steps=_SPLASH_FRAMES)
        self._render_splash_frame()
        self.set_interval(0.06, self._anim_tick)
        self.set_interval(0.2, self._poll)
        self.set_interval(0.1, self._tick_think)
        self.bridge.start()
        self._render_topbar()
        self._render_rolepanel()
        self._update_status()

    # ------------------------------------------------------------------ #
    # Splash（启动门面）
    # ------------------------------------------------------------------ #
    def _render_splash_frame(self) -> None:
        if not self._splash_frames:
            return
        splash = self.query_one("#splash_content", Static)
        idx = min(self._splash_idx, len(self._splash_frames) - 1)
        splash.update(self._splash_frames[idx])

    def _anim_tick(self) -> None:
        if self._splash_idx >= len(self._splash_frames):
            return
        self._splash_idx += 1
        if self._splash_idx >= len(self._splash_frames):
            self._render_final_splash()
        else:
            self._render_splash_frame()

    def _render_final_splash(self) -> None:
        splash = self.query_one("#splash_content", Static)
        # Textual 的块对齐（Strip.align）会把所有行当一个矩形整体居中，
        # 块内行左对齐——只有最长那行看起来居中。因此这里逐行 Rich justify。
        water = self._splash_frames[-1].copy()
        water.justify = "center"
        wordmark = gradient_text(" c h u a n - o s ")
        wordmark.justify = "center"
        tagline = Text(
            f"Stream never stops · {_VERSION}", style=MUTED, justify="center")
        hint = Text.from_markup(
            f"[{DIM}]Press [b]Enter[/b] to start · /help · Tab role[/]",
            justify="center")
        splash.update(Group(
            Text(),
            water,
            Text(),
            wordmark,
            tagline,
            Text(),
            hint,
        ))

    def action_show_splash(self) -> None:
        """Ctrl+L 重看门面。"""
        self._splash_dismissed = False
        self.query_one("#splash").display = True
        self._splash_idx = 0
        self._render_splash_frame()
        self.set_focus(None)

    def _dismiss_splash(self) -> None:
        if self._splash_dismissed:
            return
        self._splash_dismissed = True
        self.query_one("#splash").display = False
        self._update_input_state()

    async def on_key(self, event: events.Key) -> None:
        if not self._splash_dismissed:
            event.stop()
            event.prevent_default()
            self._dismiss_splash()
            return
        if event.key == "escape" and self._busy:
            event.stop()
            event.prevent_default()
            self._interrupt_turn()

    # ------------------------------------------------------------------ #
    # 轮询：进度事件 / 就绪状态 / 状态栏
    # ------------------------------------------------------------------ #
    def _poll(self) -> None:
        if not self.is_running:
            return
        try:
            self._poll_body()
        except NoMatches:
            # teardown 竞态：控件已卸载，静默退出
            return

    def _poll_body(self) -> None:
        # 首次就绪：缓存角色表并渲染
        if self.bridge.ready and not self._roster:
            self._roster = dict(self.bridge.workers())
            self._mem_count = self.bridge.memory_note_count()
            self._render_rolepanel()
            self._render_topbar()
        for ev in self.bridge.drain_events():
            if ev.get("event") == "alert":
                prefix = "alert-fail" if ev.get("error") else "alert"
                self._system_line(
                    f"[{prefix} · {escape(str(ev.get('job', '')))}] "
                    f"{escape(str(ev.get('content', '')))}")
            elif ev.get("event") == "delegate_done":
                self._render_delegate_done(ev)
            elif self._turn is not None:
                line = _format_event(ev)
                self._turn["events"].append(line)
                self._turn["details"].update("\n".join(self._turn["events"]))
                self._scroll_chat()
        if self.bridge.error and not self._roster:
            self._system_line(f"✕ wake-up failed: {escape(self.bridge.error)}")
        self._update_status()

    def _tick_think(self) -> None:
        if not self.is_running or self._busy is not True or self._turn is None:
            return
        try:
            elapsed = time.monotonic() - self._turn["start"]
            role = escape(self._turn.get("role", "幕僚长"))
            self._turn["think"].update(
                f"[{VIOLET}]├ ◌ {role} 正在思考… {elapsed:.1f}s[/]")
        except NoMatches:
            return

    def _update_status(self) -> None:
        status = self.query_one("#status", Static)
        if not self.bridge.ready:
            if self.bridge.error:
                status.update(f"[red]✕ {escape(self.bridge.error)}[/]")
            else:
                status.update(f"[{VIOLET}]◌ 唤醒幕僚长…[/]")
        else:
            uptime = int(time.monotonic() - self._start_time)
            parts = [f"[{GREEN}]● 系统 OK[/]"]
            brain = self.bridge.brain_name()
            if brain:
                parts.append(f"脑 {escape(brain)}")
            parts.append(f"记忆 {self._mem_count} 条")
            consolidating = self.bridge.consolidation_status()
            if consolidating:
                parts.append(escape(consolidating))
            parts.append(f"班底 {len(self._roster)}")
            running_bg = [
                t for t in self.bridge.delegate_snapshot()
                if t["status"] == "running"
            ]
            if running_bg:
                parts.append(f"[yellow]bg {len(running_bg)}[/]")
            mon = self.bridge.monitor_status().get("stats", {})
            mon_active = mon.get("active", 0)
            mon_dead = mon.get("dead_ends", 0)
            if mon_active or mon_dead:
                if mon_dead:
                    parts.append(f"[red]mon {mon_active}▶ {mon_dead}⚠[/]")
                else:
                    parts.append(f"[yellow]mon {mon_active}▶[/]")
            parts.append(f"运行 {uptime // 60}:{uptime % 60:02d}")
            if self._busy:
                parts.append(f"[{VIOLET}]忙[/]")
            status.update(" · ".join(parts))
        self._update_input_state()

    def _update_input_state(self) -> None:
        inp = self.query_one("#input", Input)
        inp.disabled = self._busy or not self.bridge.ready or not self._splash_dismissed
        if not inp.disabled and not inp.has_focus:
            try:
                inp.focus()
            except Exception:  # noqa: BLE001 - focus 在挂载前可能失败
                pass

    # ------------------------------------------------------------------ #
    # 顶栏 / 角色条
    # ------------------------------------------------------------------ #
    def _render_topbar(self) -> None:
        top = self.query_one("#topbar", Static)
        bar = chuan_mark()
        bar.append("  ")
        bar.append(gradient_text("chuan-os"))
        bar.append("  │  ", style=BORDER)
        bar.append(f"幕僚长 · 会话 {self.session_id}", style=MUTED)
        bar.append("  │  ", style=BORDER)
        if self._locked:
            disp = self._roster.get(self._locked, self._locked)
            bar.append(f"锁定 · {disp}", style=role_color(self._locked, disp))
        else:
            bar.append("自动路由", style=BLUE)
        brain = self.bridge.brain_name()
        if brain:
            bar.append("  │  ", style=BORDER)
            bar.append(f"脑 {brain}", style=MUTED)
        top.update(bar)

    def _render_rolepanel(self) -> None:
        """渲染左侧班底面板：auto-route + 全部角色（按名字排序）。"""
        panel = self.query_one("#rolepanel", VerticalScroll)
        title = self.query_one("#rolepanel_title", Static)
        title.update(f"角色班底 · {len(self._roster)}")
        for child in list(panel.children):
            if child.id != "rolepanel_title":
                child.remove()
        panel.mount(RoleTab(None, "自动路由", MUTED, "○",
                            selected=self._locked is None))
        for name in sorted(self._roster.keys()):
            disp = self._roster[name]
            panel.mount(RoleTab(
                name, disp, role_color(name, disp), role_avatar(name, disp),
                selected=self._locked == name,
            ))

    def select_role(self, name: str | None) -> None:
        """点击角色标签：锁定该角色；点 auto 回到自动路由。"""
        self._locked = name
        self._render_rolepanel()
        self._render_topbar()

    def action_cycle_role(self) -> None:
        """Tab 循环：auto → 各角色 → auto…"""
        if not self._roster:
            return
        opts: list[str | None] = [None] + sorted(self._roster.keys())
        if len(opts) < 2:
            return
        self._cycle_idx = (self._cycle_idx + 1) % len(opts)
        self.select_role(opts[self._cycle_idx])

    def action_command_palette(self) -> None:
        """Ctrl+P 命令面板：字母键执行对应动作，Esc 关闭。"""
        commands = [
            ("清除对话", "c", lambda: self._handle_command("/clear")),
            ("自动路由", "a", lambda: self._handle_command("/auto")),
            ("角色班底", "w", lambda: self._handle_command("/workers")),
            ("重看门面", "s", self.action_show_splash),
            ("帮助", "h", lambda: self._handle_command("/help")),
            ("退出", "q", lambda: self._handle_command("/exit")),
        ]
        self.push_screen(CommandPalette(commands))

    # ------------------------------------------------------------------ #
    # 对话区
    # ------------------------------------------------------------------ #
    def _scroll_chat(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        chat.scroll_end(animate=False)

    def _system_line(self, text: str) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(Static(f"[{DIM}]{text}[/]", classes="msg"))
        self._scroll_chat()

    def _render_delegate_done(self, ev: dict[str, Any]) -> None:
        """后台委派完成事件 → 对话区提示（完成/失败 + 结果摘要）。"""
        ok = bool(ev.get("success"))
        mark = "🟢" if ok else "🔴"
        color = GREEN if ok else "red"
        agent = escape(str(ev.get("agent", "")))
        head = escape(_shorten(str(ev.get("task", "")), 34))
        status = "完成" if ok else "失败"
        self._system_line(
            f"[{color}]{mark} 后台任务 · {agent}[/] 「{head}」{status} "
            f"[{MUTED}]({ev.get('task_id', '')})[/]")
        result = str(ev.get("result", "")).strip()
        if result:
            self._system_line(f"[{color}]⎿[/] {escape(_shorten(result, 140))}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        event.input.value = ""
        if not message:
            return
        if message.startswith("/"):
            self._handle_command(message)
            return
        if self._busy:
            self._system_line("busy… (wait for current turn)")
            return
        if not self.bridge.ready:
            self._system_line("Chief of Staff is waking up…")
            return
        await self._start_turn(message)

    async def _start_turn(self, message: str) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        self._busy = True
        user = Static(
            f"[{DIM}]你>[/] [{DEFAULT_COLOR}]{escape(message)}[/]", classes="msg")
        route = Static("", classes="msg")
        details = Static("", classes="msg")
        think = Static(f"[{VIOLET}]├ ◌ 思考中…[/]", classes="msg")
        await chat.mount(user, route, details, think)
        self._scroll_chat()
        self._turn = {
            "route": route, "details": details, "think": think,
            "events": [], "start": time.monotonic(), "role": "幕僚长",
        }
        self._update_input_state()
        self._turn_task = asyncio.create_task(self._run_turn(message))

    def _interrupt_turn(self) -> None:
        """Esc 软中断：取消当前回合的等待，dispatch 线程继续后场跑完。"""
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()

    async def _run_turn(self, message: str) -> None:
        turn = self._turn
        # 路由预览（锁定的直接显示目标；未锁定先显示关键词路由或 LLM 提示）
        preview = self._locked or self.bridge.route_preview(message)
        if preview:
            disp = self._roster.get(preview, preview)
            turn["role"] = disp
            turn["route"].update(
                f"[{BLUE}]├ 路由[/] [{MUTED}]幕僚长 → {escape(disp)}"
                + (" · 锁定[/]" if self._locked else " · 关键词[/]"))
        else:
            turn["route"].update(f"[{BLUE}]├ 路由[/] [{MUTED}]幕僚长 → ？ · LLM 选岗…[/]")
        try:
            if self._locked:
                result = await self.bridge.send_to(self._locked, message)
                result.setdefault("route", self._locked)
                result.setdefault("route_method", "locked")
            else:
                result = await self.bridge.send(message)
        except asyncio.CancelledError:
            # 软中断：放弃等待（底层 dispatch 线程无法强杀，继续在后场跑完）
            self._finish_turn(error="已中断（Esc 软中断）")
            raise
        except Exception as exc:  # noqa: BLE001 - 会话不被单次失败打断
            self._finish_turn(error=str(exc))
            return
        self._finish_turn(result=result)

    def _finish_turn(self, result: dict[str, Any] | None = None,
                     error: str | None = None) -> None:
        turn = self._turn
        chat = self.query_one("#chat", VerticalScroll)
        if turn is None:
            return
        turn["think"].remove()
        events = turn["events"]
        elapsed = time.monotonic() - turn["start"]

        if error is not None:
            turn["details"].remove()
            turn["route"].update(f"[{BLUE}]├ 路由[/] [red]失败[/]")
            chat.mount(Static(f"[red]✕ {escape(error)}[/]", classes="msg"))
        else:
            route_name = result.get("route", "chief_of_staff")
            method = result.get("route_method", "")
            disp = self._roster.get(route_name, route_name)
            color = role_color(route_name, disp)
            label = _METHOD_LABELS.get(method, method or "幕僚长")
            turn["route"].update(
                f"[{BLUE}]├ 路由[/] [{MUTED}]幕僚长 → {escape(disp)}"
                f" · {escape(label)} · {elapsed:.1f}s[/]")
            # 事件流收进可折叠路由树
            turn["details"].remove()
            if events:
                summary = f"⏺ {len(events)} 事件 · {elapsed:.1f}s"
                chat.mount(Collapsible(
                    Static("\n".join(events)), title=summary, collapsed=True))
            reply = _last_message(result)
            chat.mount(Static(
                f"[{color} bold]{escape(disp)}>" + f"[/] [{TEXT}]{escape(reply)}[/]",
                classes="msg"))
        self._scroll_chat()

        self._busy = False
        self._turn = None
        self._mem_count = self.bridge.memory_note_count()
        self._render_topbar()
        self._update_status()

    # ------------------------------------------------------------------ #
    # 斜杠命令
    # ------------------------------------------------------------------ #
    def _handle_command(self, cmd: str) -> None:
        if cmd in ("/exit", "/quit"):
            self.exit()
            return
        if cmd == "/help":
            self._system_line(
                "/help · /workers roster · /auto auto-route · /clear chat · "
                "/bg <agent> <任务> 后台委派 · /tasks 后台看板 · "
                "/mission 跨对话长任务 · /aci 预判注入面板 · /resume 断点续跑 · "
                "/mcp 管理面板 · /monitor 监督者面板 · /howto 知识原子队列 · "
                "/skill 技能队列 · "
                "/routine 例行任务 · /exit · Tab cycle role · Ctrl+L splash")
            return
        if cmd == "/workers":
            if not self._roster:
                self._system_line("(no workers — still waking up?)")
                return
            names = ", ".join(
                f"{escape(disp)}[{DIM}]({escape(name)})[/]"
                for name, disp in self._roster.items())
            self._system_line(f"roster ({len(self._roster)}): {names}")
            return
        if cmd == "/auto":
            self.select_role(None)
            self._system_line("已切回自动路由")
            return
        if cmd == "/clear":
            self.query_one("#chat", VerticalScroll).remove_children()
            return
        # /role <name> 快捷锁定
        if cmd.startswith("/role"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 2 and parts[1] in self._roster:
                self.select_role(parts[1])
                self._system_line(f"locked → {self._roster[parts[1]]}")
            else:
                self._system_line("usage: /role <persona-name>  (see /workers)")
            return
        # /tasks 后台任务看板
        if cmd == "/tasks":
            tasks = self.bridge.delegate_snapshot()
            if not tasks:
                self._system_line("暂无后台任务。用 /bg <agent> <任务> 派发。")
                return
            self._system_line(f"后台任务 · {len(tasks)} 条")
            for t in tasks:
                mark = {"pending": "⏳", "ready": "🟡", "running": "🟡",
                        "done": "🟢", "failed": "🔴"}.get(t["status"], "⚪")
                color = {"pending": MUTED, "ready": "yellow", "running": "yellow",
                         "done": GREEN, "failed": "red"}.get(t["status"], MUTED)
                head = escape(_shorten(str(t.get("task", "")), 34))
                deps = t.get("depends_on") or []
                dep_txt = f" · 依赖 {len(deps)}" if deps else ""
                self._system_line(
                    f"[{color}]{mark} {t.get('task_id')} · "
                    f"{escape(str(t.get('agent', '')))}[/] · {t['status']}"
                    f"{dep_txt} · {head}")
            return
        # /bg <agent> <任务> 后台委派（fire-and-forget）
        if cmd.startswith("/bg "):
            rest = cmd[4:].strip()
            agent_name, sep, task = rest.partition(" ")
            if not sep or not task.strip():
                self._system_line("usage: /bg <agent> <任务> [--mission <name>]  (见 /workers)")
                return
            # N32：--mission <name> 挂到跨对话长任务看板
            mission = ""
            if "--mission" in task:
                tail = task.split("--mission", 1)[1].split(None, 1)
                if tail and tail[0].strip():
                    mission = tail[0].strip()
                task = task.split("--mission", 1)[0].strip()
            try:
                task_id = self.bridge.delegate(agent_name, task, mission=mission)
            except Exception as exc:  # noqa: BLE001 - 委派失败回显给用户
                self._system_line(f"✕ 委派失败: {escape(str(exc))}")
                return
            linked = f" · Mission {escape(mission)}" if mission else ""
            self._system_line(
                f"已派发 {escape(agent_name)} → 后台运行（{task_id}）{linked}，可继续聊天")
            return
        # /mcp [on|off <name>] MCP 管理面板
        if cmd == "/mcp" or cmd.startswith("/mcp "):
            parts = cmd.split(maxsplit=2)
            verb = parts[1] if len(parts) > 1 else ""
            if verb in ("on", "off"):
                name = parts[2] if len(parts) > 2 else ""
                if not name:
                    self._system_line(f"usage: /mcp {verb} <server-name>")
                    return
                ok = (self.bridge.mcp_connect(name) if verb == "on"
                      else self.bridge.mcp_disconnect(name))
                self._system_line(
                    f"[{'green' if ok else 'red'}]{'✓' if ok else '✕'} "
                    f"/mcp {verb} {escape(name)}[/]")
                # 面板再列一次展示最新状态
                self._render_mcp_panel()
                return
            self._render_mcp_panel()
            return
        # /monitor P1 监督者面板（执行轨迹 + 死胡同 + redirect）
        if cmd == "/monitor":
            self._render_monitor_panel()
            return
        # /aci N33 预判注入面板（路由前预取的 memory+wiki 上下文）
        if cmd == "/aci":
            self._render_aci_panel()
            return
        # /resume [<session> <worker> | clear <session>] N35 断点续跑
        if cmd == "/resume" or cmd.startswith("/resume "):
            parts = cmd.split(maxsplit=3)
            verb = parts[1] if len(parts) > 1 else ""
            if verb == "clear" and len(parts) >= 3:
                self._system_line(escape(self.bridge.resume_clear(parts[2])))
                self._render_resume_panel()
                return
            if len(parts) >= 3 and parts[1]:
                session_id, worker = parts[1], parts[2]
                self._system_line(escape(self.bridge.resume_to(worker, session_id)))
                self._render_resume_panel()
                return
            self._render_resume_panel()
            return
        # /howto [show|approve|discard <name>] N27 知识原子待确认队列
        if cmd == "/howto" or cmd.startswith("/howto "):
            parts = cmd.split(maxsplit=2)
            verb = parts[1] if len(parts) > 1 else ""
            if verb in ("show", "approve", "discard"):
                name = parts[2] if len(parts) > 2 else ""
                if not name:
                    self._system_line(f"usage: /howto {verb} <name>")
                    return
                if verb == "show":
                    self._system_line(escape(self.bridge.howto_show(name)))
                else:
                    msg = (self.bridge.howto_approve(name) if verb == "approve"
                           else self.bridge.howto_discard(name))
                    self._system_line(escape(msg))
                    self._render_howto_panel()  # 面板再列一次展示最新状态
                return
            self._render_howto_panel()
            return
        # /skill [show|approve|discard <name>] N30 技能待确认队列
        if cmd == "/skill" or cmd.startswith("/skill "):
            parts = cmd.split(maxsplit=2)
            verb = parts[1] if len(parts) > 1 else ""
            if verb in ("show", "approve", "discard"):
                name = parts[2] if len(parts) > 2 else ""
                if not name:
                    self._system_line(f"usage: /skill {verb} <name>")
                    return
                if verb == "show":
                    self._system_line(escape(self.bridge.skill_show(name)))
                else:
                    msg = (self.bridge.skill_approve(name) if verb == "approve"
                           else self.bridge.skill_discard(name))
                    self._system_line(escape(msg))
                    self._render_skill_panel()  # 面板再列一次展示最新状态
                return
            self._render_skill_panel()
            return
        # /mission [start|finish|pause|resume|remove] N32 跨对话长任务看板
        if cmd == "/mission" or cmd.startswith("/mission "):
            parts = cmd.split(maxsplit=2)
            verb = parts[1] if len(parts) > 1 else ""
            if verb == "start" and len(parts) >= 3:
                nm, _, goal = parts[2].partition(" ")
                if nm and goal.strip():
                    self._system_line(escape(self.bridge.mission_start(nm, goal.strip())))
                else:
                    self._system_line("usage: /mission start <name> <目标>")
                self._render_mission_panel()
                return
            if verb in ("finish", "pause", "resume", "remove"):
                nm = parts[2] if len(parts) > 2 else ""
                if not nm:
                    self._system_line(f"usage: /mission {verb} <name>")
                    return
                if verb == "finish":
                    _, _, summary = parts[2].partition(" ")
                    self._system_line(escape(self.bridge.mission_finish(nm, summary.strip())))
                else:
                    fn = getattr(self.bridge, f"mission_{verb}")
                    self._system_line(escape(fn(nm)))
                self._render_mission_panel()
                return
            self._render_mission_panel()
            return
        # /routine [add <name> <调度> <任务...> | remove <name>] N28 例行任务
        if cmd == "/routine" or cmd.startswith("/routine "):
            parts = cmd.split(maxsplit=3)
            verb = parts[1] if len(parts) > 1 else ""
            if verb == "add" and len(parts) >= 4:
                name, spec, task = parts[2], parts[3], ""
                # 任务文本可能含空格：从原文截取第三个分隔后的剩余部分
                head = f"/routine add {name} {spec} "
                if cmd.startswith(head):
                    task = cmd[len(head):].strip()
                archive = " --wiki" in task
                if archive:
                    task = task.replace(" --wiki", "").strip()
                retries = 0
                if "--retries" in task:
                    tail = task.split("--retries", 1)[1].split(None, 1)
                    if tail and tail[0].strip().lstrip("-").isdigit():
                        retries = int(tail[0])
                    task = task.split("--retries", 1)[0].strip()
                msg = self.bridge.routine_add(
                    name, task, spec, archive_to_wiki=archive, retries=retries)
                self._system_line(escape(msg))
                self._render_routine_panel()
                return
            if verb == "remove" and len(parts) >= 3:
                msg = self.bridge.routine_remove(parts[2])
                self._system_line(escape(msg))
                self._render_routine_panel()
                return
            self._render_routine_panel()
            return
        self._system_line(f"unknown command: {escape(cmd)}  (try /help)")

    def _render_monitor_panel(self) -> None:
        """把 P1 监督者面板渲染进对话区：执行轨迹 + 死胡同 + redirect。"""
        snap = self.bridge.monitor_status()
        stats = snap.get("stats", {})
        traces = snap.get("traces", [])
        dead_ends = snap.get("dead_ends", [])
        redirects = snap.get("redirects", [])
        self._system_line(
            f"Supervisor Monitor · {stats.get('traces', 0)} 轨迹"
            f"（{stats.get('active', 0)} 活跃）· {stats.get('dead_ends', 0)} 死胡同"
            f" · {stats.get('redirects', 0)} redirect")
        if traces:
            for t in traces:
                mark = "🟢" if t.get("active") else "⚪"
                color = "yellow" if t.get("active") else MUTED
                self._system_line(
                    f"[{color}]{mark} {escape(str(t.get('role', '')))}[/] · "
                    f"{t.get('trace_id')} · {t.get('steps', 0)} 步 · "
                    f"{t.get('elapsed', 0)}s · "
                    f"{'运行中' if t.get('active') else '完成'} · "
                    f"末步 {escape(str(t.get('last_step', '')) or '-')}")
        else:
            self._system_line(f"[{MUTED}]（暂无执行轨迹）[/]")
        if dead_ends:
            self._system_line(f"[red]死胡同 {len(dead_ends)}[/]")
            for d in dead_ends[-6:]:
                mark = {"loop": "🔁", "repeated_failure": "🔴",
                        "stagnation": "⏱"}.get(d.get("kind"), "⚠")
                self._system_line(
                    f"  {mark} {escape(str(d.get('trace_id', '')))} · "
                    f"{escape(str(d.get('step', '')))} · "
                    f"{escape(_shorten(str(d.get('reason', '')), 46))} · "
                    f"→ {escape(str(d.get('redirect', '')))}")
        if redirects:
            self._system_line(f"[yellow]redirect {len(redirects)}[/]")
            for r in redirects[-4:]:
                self._system_line(
                    f"  ↳ {escape(str(r.get('trace_id', '')))} · "
                    f"{escape(str(r.get('step', '')))} · "
                    f"{escape(str(r.get('kind', '')))}"
                    f"{f' → {escape(str(r.get("target_agent", "")))}' if r.get('target_agent') else ''}")
        self._system_line(
            f"[{MUTED}]监督者不干活只看轨迹；/monitor 随时查看[/]")

    def _render_mcp_panel(self) -> None:
        """把 MCP server 状态面板渲染进对话区。"""
        servers = self.bridge.mcp_status()
        if not servers:
            self._system_line("MCP · 未配置任何 server（config/mcp_servers.yaml）")
            return
        self._system_line(f"MCP Servers · {len(servers)} 个已配置")
        for s in servers:
            if s.get("connected"):
                mark, color = "🟢", GREEN
                tail = f"工具 {s.get('tools', 0)}"
            else:
                err = str(s.get("error", "")).strip()
                mark, color = ("🔴", "red") if err else ("⚪", MUTED)
                tail = (f"[{MUTED}]工具 0[/] · {escape(_shorten(err, 60))}"
                        if err else "未连接")
            cmd = s.get("command", "python")
            desc = s.get("description", "")
            head = escape(str(s.get("name", "")))
            self._system_line(
                f"[{color}]{mark} {head}[/] · {escape(str(cmd))}"
                f"{f' · {escape(desc)}' if desc else ''} · {tail}")
        self._system_line(
            f"[{MUTED}]用法: /mcp on <name> · /mcp off <name> · "
            f"/mcp 查看本面板[/]")

    def _render_howto_panel(self) -> None:
        """把 N27 知识原子待确认队列渲染进对话区（自动沉淀 → 人工确认）。"""
        pending = self.bridge.howto_staging()
        if not pending:
            self._system_line(
                "HowTo · 暂无待确认的知识原子。成功完成任务会自动沉淀候选。")
            return
        self._system_line(
            f"HowTo · 待确认 {len(pending)} 条（自动沉淀，人工确认后入库）")
        for i, c in enumerate(pending, 1):
            name = escape(str(c.get("name", "")))
            trigger = escape(_shorten(str(c.get("trigger", "")), 34))
            created = str(c.get("created", ""))[:16]
            src = escape(str(c.get("source", "")) or "-")
            self._system_line(
                f"[yellow]{i}[/] · [bold]{name}[/] · {trigger} · "
                f"{created} · 来源 {src}")
        self._system_line(
            f"[{MUTED}]用法: /howto show <name> · approve <name> · "
            f"discard <name>[/]")

    def _render_skill_panel(self) -> None:
        """把 N30 技能待确认队列渲染进对话区（自动沉淀 → 人工确认注册）。"""
        pending = self.bridge.skill_staging()
        status = self.bridge.skill_status()
        if not pending:
            self._system_line(
                f"Skill · 已注册 {status.get('registered', 0)} 个 prompt 技能 · "
                "暂无待确认候选。成功完成任务会自动沉淀。")
            return
        self._system_line(
            f"Skill · 待确认 {len(pending)} 条（人工确认后注册为技能）")
        for i, c in enumerate(pending, 1):
            name = escape(str(c.get("name", "")))
            kws = escape("、".join(c.get("keywords") or []) or "无")
            created = str(c.get("created", ""))[:16]
            src = escape(str(c.get("source", "")) or "-")
            self._system_line(
                f"[yellow]{i}[/] · [bold]{name}[/] · 触发 {kws} · "
                f"{created} · 来源 {src}")
        self._system_line(
            f"[{MUTED}]用法: /skill show <name> · approve <name> · "
            f"discard <name>[/]")

    def _render_mission_panel(self) -> None:
        """把 N32 跨对话长任务看板渲染进对话区（Mission 状态机）。"""
        items = self.bridge.mission_list()
        if not items:
            self._system_line(
                "Mission · 暂无长任务。用 /mission start <name> <目标> 登记；"
                "/bg <agent> <任务> --mission <name> 关联。")
            return
        self._system_line(f"Mission · {len(items)} 条跨对话长任务")
        for m in items:
            mark = {"active": "🟢", "paused": "⏸", "done": "✅",
                    "failed": "🔴"}.get(m.get("status"), "⚪")
            name = escape(str(m.get("name", "")))
            agent = escape(str(m.get("agent", "")))
            goal = escape(_shorten(str(m.get("goal", "")), 30))
            tasks = int(m.get("tasks", 0))
            updated = str(m.get("updated", ""))[:16]
            self._system_line(
                f"{mark} [bold]{name}[/] · {agent} · 任务 {tasks} · "
                f"更新 {updated} · {goal}")
            prog = str(m.get("progress", "") or "")
            if prog:
                self._system_line(f"    └ {escape(_shorten(prog, 44))}")
        self._system_line(
            f"[{MUTED}]用法: /mission start <name> <目标> · finish|pause|"
            f"resume|remove <name>[/]")

    def _render_aci_panel(self) -> None:
        """把 N33 ACI 预判注入面板渲染进对话区（最近一次预取统计）。"""
        st = self.bridge.aci_status()
        mem = int(st.get("memory", 0))
        wiki = int(st.get("wiki", 0))
        mark = "[green]✓ 已注入[/]" if st.get("injected") else "[dim]— 未注入[/]"
        self._system_line(
            f"ACI 预判注入 · 最近预取：记忆 {mem} · wiki {wiki} · "
            f"合计 {mem + wiki} · {mark}")
        self._system_line(
            f"[{MUTED}]路由前并行预取 memory+wiki 上下文，注入岗位任务文本，"
            f"让 agent 首轮直接带背景开工（减少首轮空转）[/]")

    def _render_resume_panel(self) -> None:
        """把 N35 断点续跑档案渲染进对话区（打断不丢工具看板）。"""
        items = self.bridge.resume_list()
        if not items:
            self._system_line(
                "Resume · 暂无断点档案。长任务执行中断后自动保存子任务结果，"
                "可在此续跑。")
            return
        self._system_line(f"Resume · {len(items)} 条可恢复断点（打断不丢工具）")
        for it in items:
            sid = escape(str(it.get("session_id", "")))
            role = escape(str(it.get("role", "")))
            task = escape(_shorten(str(it.get("task", "")), 30))
            done = int(it.get("done", 0))
            total = int(it.get("total", 0))
            updated = str(it.get("updated_at", ""))[:16]
            self._system_line(
                f"[bold]{sid}[/] · {role} · {done}/{total} · "
                f"更新 {updated} · {task}")
        self._system_line(
            f"[{MUTED}]用法: /resume <session> <worker> 续跑 · "
            f"/resume clear <session> 清除[/]")

    def _render_routine_panel(self) -> None:
        """把 N28 例行任务面板渲染进对话区（自转闭环的看板）。"""
        items = self.bridge.routine_list()
        if not items:
            self._system_line(
                "Routine · 暂无例行任务。用 /routine add <name> <调度> <任务> 添加。")
            return
        self._system_line(f"Routine · {len(items)} 条例行任务（到点自转）")
        for r in items:
            arch = " 📥wiki" if r.get("archive_to_wiki") else ""
            name = escape(str(r.get("name", "")))
            schedule = escape(str(r.get("schedule", "")))
            nxt = escape(str(r.get("next_run", "—")))
            agent = escape(str(r.get("agent", "")))
            msg = escape(_shorten(str(r.get("message", "")), 30))
            fc = int(r.get("fail_count") or 0)
            rt = int(r.get("retries") or 0)
            retry = f" · 🔁 retry {fc}/{rt}" if fc and rt > 0 else (
                f" · 🔁 重试 {rt} 次" if rt else "")
            self._system_line(
                f"⏱ [bold]{name}[/] · {schedule} · 下次 {nxt} · "
                f"{agent}{arch}{retry} · {msg}")
        self._system_line(
            f"[{MUTED}]用法: /routine add <name> <调度> <任务> "
            f"(调度: fri@17:30 / every@3600，加 --wiki 归档 / --retries N 重试) · "
            f"remove <name>[/]")

    def on_unmount(self) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()


def main() -> None:
    """chuan-tui 命令入口。"""
    bridge = SupervisorBridge()
    app = ChuanTUI(bridge=bridge)
    try:
        app.run()
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    main()
