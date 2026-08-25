"""川流 CLI 启动入口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chuan.runtime_supervisor import RuntimeSupervisor
from chuan.scheduler import ProactiveAlert
from chuan.channels.hud import HudChannel, push_monitor_snapshot

Input = Callable[[str], str]
Output = Callable[[str], None]


def _last_message(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "（没有返回内容）"
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    return str(content or "（没有返回内容）")


def run_cli(
    *,
    supervisor_factory: Callable[..., RuntimeSupervisor] = RuntimeSupervisor,
    input_fn: Input = input,
    output_fn: Output = print,
) -> None:
    """运行可测试的交互循环。"""
    output_fn("川流 chuan-os v0.1.0")
    output_fn("幕僚长正在醒来…")

    def show_alert(alert: ProactiveAlert) -> None:
        prefix = "主动任务失败" if alert.error else "管家提醒"
        output_fn(f"\n[{prefix} · {alert.job_name}] {alert.content}")

    supervisor = supervisor_factory(on_proactive_alert=show_alert)
    hud = HudChannel()

    # 后台委派（fire-and-forget）完成回推：异步线程触发，直接 print + HUD
    def show_delegate_done(info: dict[str, Any]) -> None:
        status = "完成" if info.get("success") else "失败"
        head = (info.get("task") or "")[:30]
        output_fn(
            f"\n[后台任务 · {info.get('agent')}] 「{head}」{status}（{info.get('task_id')}）"
        )
        result = (info.get("result") or "").strip()
        if result:
            output_fn(result[:800])
        hud.effect("success" if info.get("success") else "error")
        if result:
            hud.show_ai_text(f"[{info.get('agent')}] {result[:200]}")

    harness = getattr(supervisor, "agent_harness", None)
    if harness is not None:
        harness.on_done(show_delegate_done)

    if hud.alive:
        hud.wake()
        # N34 SCENE 协议握手：连接后推 hello（caps 协商）+ 全量 scene，供前端/未来 PWA 初始化
        if getattr(hud, "scene_enabled", False):
            hud.send_hello()
            hud.send_scene_full()
        output_fn(f"[HUD] Jarvis 全息悬浮层在线（{hud.endpoint}）")
    try:
        supervisor.wake_up()
        output_fn("幕僚长已就绪。输入 /help 查看命令，输入 exit 退出。")
        while True:
            try:
                message = input_fn("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                output_fn("\n再见。")
                break

            if not message:
                continue
            if message.lower() in {"exit", "quit", "/exit", "/quit"}:
                output_fn("再见。")
                break
            if message == "/help":
                output_fn(
                    "/voice 语音模式；/alerts 主动提醒；/workers 成员；"
                    "/team <任务> 多岗位并行协作；"
                    "/bg <agent> <任务> 后台委派；/tasks 后台任务看板；"
                    "/mission 跨对话长任务看板；/aci 预判注入面板；"
                    "/resume 断点续跑；"
                    "/monitor 监督者面板；/howto 知识原子待确认队列；"
                    "/skill 技能待确认队列；"
                    "/routine 例行任务（add/remove/list）；"
                    "/tools 工具市场（enable/disable/select）；exit 退出。"
                )
                continue
            if message == "/voice":
                output_fn("切换到语音模式…")
                from chuan.voice.main import run_voice_mode

                run_voice_mode()
                output_fn("已退出语音模式。")
                continue
            if message == "/alerts":
                alerts = supervisor.scheduler.get_alerts(clear=True)
                if not alerts:
                    output_fn("暂无主动提醒。")
                for alert in alerts:
                    show_alert(alert)
                continue
            if message == "/workers":
                output_fn("可用成员：" + "、".join(supervisor.list_workers()))
                continue
            if message == "/tasks":
                delegate_snapshot = getattr(supervisor, "delegate_snapshot", None)
                if delegate_snapshot is None:
                    output_fn("当前幕僚长不支持后台任务看板。")
                    continue
                tasks = delegate_snapshot()
                if not tasks:
                    output_fn("暂无后台任务。用 /bg <agent> <任务> 派发。")
                for t in tasks:
                    mark = {"running": "🟡", "done": "🟢", "failed": "🔴"}.get(
                        t["status"], "⚪"
                    )
                    output_fn(
                        f"{mark} {t['task_id']} · {t['agent']} · {t['status']}"
                        f" · {t['task'][:40]}"
                    )
                continue
            if message.startswith("/bg "):
                delegate = getattr(supervisor, "delegate", None)
                if delegate is None:
                    output_fn("当前幕僚长不支持后台委派。")
                    continue
                rest = message[4:].strip()
                agent_name, sep, task = rest.partition(" ")
                if not sep or not task.strip():
                    output_fn("用法：/bg <agent> <任务> [--mission <name>]，例如 /bg claude_code 写个脚本")
                    continue
                # N32：--mission <name> 把后台任务挂到跨对话长任务看板
                mission = ""
                if "--mission" in task:
                    tail = task.split("--mission", 1)[1].split(None, 1)
                    if tail and tail[0].strip():
                        mission = tail[0].strip()
                    task = task.split("--mission", 1)[0].strip()
                try:
                    task_id = delegate(agent_name, task, mission=mission)
                except KeyError as exc:
                    output_fn(f"[委派失败] {exc}")
                    continue
                linked = f"（Mission：{mission}）" if mission else ""
                output_fn(f"已派发给 {agent_name}，后台运行中（{task_id}）{linked}。可继续聊天。")
                continue
            if message == "/mission":
                ml = getattr(supervisor, "mission_list", None)
                if ml is None:
                    output_fn("当前幕僚长不支持 Mission 看板。")
                    continue
                items = ml()
                if not items:
                    output_fn("暂无 Mission。用 /mission start <name> <目标> 登记；/bg <agent> <任务> --mission <name> 关联。")
                    continue
                output_fn(f"Mission 看板 · {len(items)} 条")
                for m in items:
                    mark = {"active": "🟢", "paused": "⏸", "done": "✅", "failed": "🔴"}.get(
                        m.get("status"), "⚪")
                    prog = (m.get("progress") or "")[:36] or "（无进度）"
                    output_fn(
                        f"{mark} {m['name']} · {m.get('agent')} · "
                        f"任务 {m.get('tasks', 0)} · 更新 {str(m.get('updated', ''))[:16]}"
                    )
                    output_fn(f"    目标：{m.get('goal', '')[:60]}")
                    if m.get("progress"):
                        output_fn(f"    进度：{prog}")
                output_fn("用法：/mission start|finish|pause|resume|remove <name> [目标/摘要]")
                continue
            if message.startswith("/mission "):
                parts = message[len("/mission "):].split(None, 1)
                action, rest = parts[0], (parts[1].strip() if len(parts) > 1 else "")
                fn = getattr(supervisor, f"mission_{action}", None)
                if fn is None:
                    output_fn("用法：/mission start|finish|pause|resume|remove <name> [目标/摘要]")
                    continue
                if action == "start":
                    nm, _, goal = rest.partition(" ")
                    output_fn(fn(nm, goal.strip()) if nm and goal.strip() else
                              "用法：/mission start <name> <目标>")
                elif action == "finish":
                    nm, _, summary = rest.partition(" ")
                    output_fn(fn(nm, summary.strip()) if nm else
                              "用法：/mission finish <name> [结果摘要]")
                else:
                    output_fn(fn(rest) if rest else f"用法：/mission {action} <name>")
                continue
            if message == "/monitor":
                monitor_status = getattr(supervisor, "monitor_status", None)
                if monitor_status is None:
                    output_fn("当前幕僚长不支持监督者面板。")
                    continue
                snap = monitor_status()
                stats = snap.get("stats", {})
                output_fn(
                    f"监督者面板 · {stats.get('traces', 0)} 轨迹"
                    f"（{stats.get('active', 0)} 活跃）· {stats.get('dead_ends', 0)} 死胡同"
                    f" · {stats.get('redirects', 0)} redirect"
                )
                for tr in snap.get("traces", []):
                    last = "…" if tr.get("last_step") else "—"
                    mark = "🟡" if tr.get("active") else ("🟢" if tr.get("last_ok") else "🔴")
                    output_fn(
                        f"  {mark} {tr['trace_id']} · {tr['role']} · {tr['steps']} 步"
                        f" · {tr.get('elapsed', 0)}s · {last}"
                    )
                for de in snap.get("dead_ends", [])[-5:]:
                    output_fn(f"  ⛔ 死胡同 {de['trace_id']}·{de['step']} [{de['kind']}] → {de['redirect']}")
                for rd in snap.get("redirects", [])[-5:]:
                    extra = rd.get("target_agent") or (rd.get("hint", "")[:20])
                    output_fn(f"  ↪ redirect {rd['trace_id']}·{rd['step']} [{rd['kind']}] → {extra}")
                continue
            if message == "/aci":
                aci_status = getattr(supervisor, "aci_status", None)
                if aci_status is None:
                    output_fn("当前幕僚长不支持 ACI 预判注入面板。")
                    continue
                st = aci_status()
                output_fn(
                    f"ACI 预判注入 · 最近预取：记忆 {st.get('memory', 0)}"
                    f" · wiki {st.get('wiki', 0)} · 合计 {st.get('total', 0)}"
                    f" · {'已注入' if st.get('injected') else '未注入'}"
                )
                output_fn(
                    "路由前并行预取 memory+wiki 上下文，注入岗位任务文本，"
                    "让 agent 首轮直接带背景开工（减少首轮空转）。"
                )
                continue
            if message == "/resume":
                resume_list = getattr(supervisor, "resume_list", None)
                if resume_list is None:
                    output_fn("当前幕僚长不支持断点续跑。")
                    continue
                items = resume_list()
                if not items:
                    output_fn("暂无断点档案。长任务执行中断后会自动保存子任务结果，可在此续跑。")
                    continue
                output_fn(f"断点续跑 · {len(items)} 条可恢复")
                for i, it in enumerate(items, 1):
                    output_fn(
                        f"{i}. [{it['session_id']}] {it.get('role')} · "
                        f"进度 {it.get('done', 0)}/{it.get('total', 0)} · "
                        f"{it.get('task', '')[:40]} · {it.get('updated_at', '')[:16]}")
                output_fn(
                    "用法：/resume <session_id> <worker>（续跑）· "
                    "/resume clear <session_id>（清除）")
                continue
            if message.startswith("/resume "):
                resume_to = getattr(supervisor, "resume_to", None)
                resume_clear = getattr(supervisor, "resume_clear", None)
                parts = message[len("/resume "):].split(maxsplit=2)
                if parts and parts[0] == "clear" and len(parts) >= 2 and resume_clear:
                    output_fn(resume_clear(parts[1]))
                    continue
                if len(parts) >= 2 and resume_to is not None:
                    session_id, worker = parts[0], parts[1]
                    output_fn(resume_to(worker, session_id))
                    continue
                output_fn("用法：/resume <session_id> <worker> · /resume clear <session_id>")
                continue
            if message == "/howto":
                staging = getattr(supervisor, "howto_staging", None)
                if staging is None:
                    output_fn("当前幕僚长不支持知识原子队列。")
                    continue
                pending = staging()
                if not pending:
                    output_fn("暂无待确认的知识原子。成功完成任务会自动沉淀候选。")
                    continue
                for i, c in enumerate(pending, 1):
                    output_fn(
                        f"{i}. [{c['name']}] 触发：{c.get('trigger', '')[:30]}"
                        f" · {c.get('created', '')[:10]} · 来源 {c.get('source', '')}"
                    )
                output_fn("用法：/howto show <name> /howto approve <name> [/howto discard <name>]")
                continue
            if message.startswith("/howto "):
                parts = message[len("/howto "):].split(None, 1)
                action, name = parts[0], (parts[1].strip() if len(parts) > 1 else "")
                cmd = getattr(supervisor, f"howto_{action}", None)
                if action in {"approve", "discard", "show"} and name and cmd is not None:
                    output_fn(cmd(name))
                else:
                    output_fn("用法：/howto show|approve|discard <name>")
                continue
            if message == "/skill":
                staging = getattr(supervisor, "skill_staging", None)
                if staging is None:
                    output_fn("当前幕僚长不支持技能队列。")
                    continue
                pending = staging()
                status = getattr(supervisor, "skill_status", lambda: {})()
                if not pending:
                    output_fn(
                        f"暂无待确认技能（已注册 prompt 技能 {status.get('registered', 0)} 个）。"
                        "成功完成任务会自动沉淀候选。")
                    continue
                for i, c in enumerate(pending, 1):
                    kws = "、".join(c.get("keywords") or []) or "无"
                    output_fn(
                        f"{i}. [{c['name']}] 关键词：{kws}"
                        f" · {c.get('created', '')[:10]} · 来源 {c.get('source', '')}"
                    )
                output_fn("用法：/skill show <name> /skill approve <name> [/skill discard <name>]")
                continue
            if message.startswith("/skill "):
                parts = message[len("/skill "):].split(None, 1)
                action, name = parts[0], (parts[1].strip() if len(parts) > 1 else "")
                cmd = getattr(supervisor, f"skill_{action}", None)
                if action in {"approve", "discard", "show"} and name and cmd is not None:
                    output_fn(cmd(name))
                else:
                    output_fn("用法：/skill show|approve|discard <name>")
                continue
            if message == "/routine":
                rl = getattr(supervisor, "routine_list", None)
                if rl is None:
                    output_fn("当前幕僚长不支持例行任务。")
                    continue
                items = rl()
                if not items:
                    output_fn("暂无例行任务。用 /routine add <name> <调度> <任务> 添加。")
                    continue
                for r in items:
                    arch = " 📥wiki" if r.get("archive_to_wiki") else ""
                    retry = ""
                    fc = r.get("fail_count") or 0
                    if fc and (r.get("retries") or 0) > 0:
                        retry = f" · 🔁 retry {fc}/{r.get('retries')}"
                    elif r.get("retries"):
                        retry = f" · 🔁 重试 {r.get('retries')} 次"
                    output_fn(
                        f"⏱ {r['name']} · {r['schedule']} · 下次 {r.get('next_run', '—')}"
                        f" · {r['agent']}{arch}{retry} · {r['message'][:40]}"
                    )
                continue
            if message.startswith("/routine add "):
                ra = getattr(supervisor, "routine_add", None)
                if ra is None:
                    output_fn("当前幕僚长不支持例行任务。")
                    continue
                # /routine add <name> <调度> <任务...>，调度用紧凑写法 fri@17:30 / every@3600
                parts = message[len("/routine add "):].split(maxsplit=2)
                if len(parts) < 3:
                    output_fn("用法：/routine add <name> <调度> <任务>（调度：fri@17:30 / every@3600）")
                    continue
                name, spec, task = parts[0], parts[1], parts[2].strip()
                archive = " --wiki" in task
                if archive:
                    task = task.replace(" --wiki", "").strip()
                retries = 0
                if "--retries" in task:
                    tail = task.split("--retries", 1)[1].split(None, 1)
                    retries = int(tail[0]) if tail and tail[0].strip().lstrip("-").isdigit() else 0
                    task = task.split("--retries", 1)[0].strip()
                output_fn(ra(name, task, spec, archive_to_wiki=archive, retries=retries))
                continue
            if message.startswith("/routine remove "):
                rr = getattr(supervisor, "routine_remove", None)
                if rr is None:
                    output_fn("当前幕僚长不支持例行任务。")
                    continue
                name = message[len("/routine remove "):].strip()
                output_fn(rr(name) if name else "用法：/routine remove <name>")
                continue
            if message == "/tools":
                market = getattr(supervisor, "tool_market_status", None)
                if market is None:
                    output_fn("当前幕僚长不支持工具市场。")
                    continue
                st = market()
                if not st.get("enabled"):
                    output_fn(
                        f"工具市场未开启（config.yaml 的 tool_market.enabled: false）。"
                        f"开启后：/tools 查看目录，/tools enable|disable <name> 上下架，"
                        f"/tools select <任务> 预览按信号裁剪。当前全量挂载 {st.get('total', 0)} 个工具（ADR-009）。"
                    )
                    continue
                cat = st.get("catalog", [])
                disabled = set(st.get("disabled", []))
                output_fn(
                    f"工具市场 · 目录 {st.get('total', 0)} 个 · 上架 {st.get('active', 0)}"
                    f" · 下架 {len(disabled)} · min_tools {st.get('min_tools', 0)}"
                )
                for c in cat:
                    mark = "✕" if c["name"] in disabled else "✓"
                    output_fn(f"  {mark} {c['name']} [{c['source']}] {c['description'][:40]}")
                output_fn("用法：/tools enable|disable <name> /tools select <任务>")
                continue
            if message.startswith("/tools "):
                parts = message[len("/tools "):].split(None, 1)
                action, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")
                if action in {"enable", "disable"} and arg:
                    toggle = getattr(supervisor.tool_market, action, None)
                    ok = toggle(arg) if toggle is not None else False
                    output_fn(
                        f"已{'上架' if action == 'enable' else '下架'}「{arg}」"
                        if ok else f"未找到工具「{arg}」"
                    )
                    continue
                if action == "select":
                    sel = getattr(supervisor, "tool_market_select", None)
                    names = sel(arg, min_tools=6) if sel is not None and arg else []
                    if not arg:
                        output_fn("用法：/tools select <任务文本>")
                    else:
                        output_fn(f"按信号裁剪（{len(names)} 个）：" + "、".join(names))
                    continue
                if action == "refresh":
                    refresh = getattr(supervisor.tool_market, "refresh", None)
                    if refresh is not None:
                        refresh()
                        output_fn("已重建工具市场目录（MCP 增删后生效）。")
                    continue
                output_fn("用法：/tools enable|disable <name> /tools select <任务> /tools refresh")
                continue

            try:
                reply = _last_message(supervisor.dispatch(message))
                hud.show_user_text(message)
                hud.effect("processing")
                hud.show_ai_text(reply)
                push_monitor_snapshot(supervisor, hud)
                output_fn("川流> " + reply)
            except Exception as exc:  # noqa: BLE001 - keep the interactive session alive
                output_fn(f"[请求失败] {exc}")
    finally:
        supervisor.shutdown()


def cli() -> None:
    """命令行脚本入口。"""
    run_cli()


if __name__ == "__main__":
    cli()
