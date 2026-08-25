"""N48 局域网 HTTPS + 手机 PWA 接入网关（ADR-043）。

在既有 HUD（TCP → Flutter 悬浮层）之上新增一个 Web 旁路，让手机在
同局域网经 HTTPS 访问 PWA 并下发/接收 HUD 命令：

- **HTTP/HTTPS 静态服务**：把 `web/` 的 PWA（manifest + Service Worker）推给手机；
  自签证书缺失/加载失败 → **静默退回纯 HTTP** 并打警告（旁路降级，不阻断）。
- **SCENE 协议 WebSocket**（`/ws`）：与 N34 同一套 scene 状态（agent/effect/
  user/ai/monitor），同一协议 TCP/WebSocket 只换传输层——手机 PWA 复用 Flutter
  悬浮层那套帧（`hello`/`scene`/`patch`），共享 load-bearing 状态。
- **API**：
  - `POST /api/message`：把手机输入经 `RuntimeSupervisor.dispatch` 路由，回复
    回传，并同时把 user/ai/effect 打平到所有 WebSocket 客户端（及 HUD）；
  - `POST /api/hud`：显式下发一条 HUD 命令（wake/hide/agent/effect/user/ai/monitor）；
  - `GET  /api/health`：后端存活探针（手机 PWA 判断连接用）。

旁路设计（对齐项目惯例：故障静默降级、旁路增强）：
- 未绑定 supervisor/hud → 对应 API 返回明确错误码，但静态页/WS 仍可用；
- WebSocket 客户端断开即从池移除，发送失败不回抛；
- 任何 handler 异常都被捕获转成 JSON 错误，绝不拖垮主进程。

独立启动：`python -m chuan.gateway.http_gateway [--supervisor]`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from aiohttp import WSMsgType, web

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8443
SCENE_VERSION = 1
SCENE_CAPS = (
    "hello", "scene", "patch", "welcome", "wake", "hide",
    "agent", "user", "ai", "effect", "monitor", "message",
)


class HttpGateway:
    """Web 接入旁路：HTTPS 静态 PWA + SCENE WebSocket + /api/message /api/hud。

    可独立启动（无 supervisor/hud，只服务静态页），也可 ``attach(supervisor, hud)``
    挂到真实幕僚长与 HUD 上，让手机真正把话接进 agent。
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        config_path: str | Path = "config/config.yaml",
        *,
        supervisor: Any = None,
        hud: Any = None,
    ) -> None:
        self._root = Path(__file__).resolve().parent.parent.parent
        if config is None:
            config = self._load_config(config_path).get("http", {}) or {}
        config = config or {}
        self.enabled = bool(config.get("enabled", True))
        self.host = str(config.get("host", _DEFAULT_HOST))
        self.port = int(config.get("port", _DEFAULT_PORT))
        self.tls = bool(config.get("tls", True))
        self.cert_path = str(self._resolve(config.get("cert_path", "certs/https_cert.pem")))
        self.key_path = str(self._resolve(config.get("key_path", "certs/https_key.pem")))
        self.web_root = self._resolve(config.get("web_root", "web/"))

        self.supervisor = supervisor
        self.hud = hud
        self._harness_wired: Any = None

        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: Any = None
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._local_scene = self._new_scene()
        self.url = ""
        self._setup_routes()

    # ------------------------------------------------------------------ #
    # 配置解析
    # ------------------------------------------------------------------ #
    def _load_config(self, path: str | Path) -> dict[str, Any]:
        p = Path(path)
        if not p.is_absolute():
            p = self._root / p
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _resolve(self, p: Any) -> Path:
        path = Path(str(p))
        return path if path.is_absolute() else (self._root / path).resolve()

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def _setup_routes(self) -> None:
        r = self._app.router
        r.add_get("/", self._handle_index)
        r.add_get("/api/health", self._handle_health)
        r.add_get("/ws", self._handle_ws)
        r.add_post("/api/message", self._handle_message)
        r.add_post("/api/hud", self._handle_hud)
        r.add_get("/{path:.*}", self._handle_asset)

    # ------------------------------------------------------------------ #
    # 静态资源（PWA 外壳）
    # ------------------------------------------------------------------ #
    async def _handle_index(self, request: web.Request) -> web.Response:
        return await self._serve_file("index.html")

    async def _handle_asset(self, request: web.Request) -> web.Response:
        rel = request.match_info.get("path") or ""
        return await self._serve_file(rel)

    async def _serve_file(self, rel: str) -> web.Response:
        if not rel or rel.endswith("/"):
            return web.Response(status=404)
        base = self.web_root.resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)):  # 防目录穿越
            return web.Response(status=404)
        if not target.is_file():
            return web.Response(status=404)
        if rel.endswith(".webmanifest"):
            return web.Response(body=target.read_bytes(), content_type="application/manifest+json")
        return web.FileResponse(target)

    # ------------------------------------------------------------------ #
    # 健康探针
    # ------------------------------------------------------------------ #
    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "tls": self._build_ssl_context() is not None,
            "supervisor": self.supervisor is not None,
            "hud": self.hud is not None,
            "ws_clients": len(self._ws_clients),
            "scene": self._scene_snapshot(),
        })

    # ------------------------------------------------------------------ #
    # SCENE 协议 WebSocket
    # ------------------------------------------------------------------ #
    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
        await ws.prepare(request)
        self._ws_clients.add(ws)
        try:
            await self._safe_send(ws, self._frame("hello", {
                "client": "chuan-os", "version": SCENE_VERSION,
                "caps": list(SCENE_CAPS),
            }))
            scene = self._scene_snapshot()
            if scene:
                await self._safe_send(ws, self._frame("scene", scene))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._on_ws_text(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._ws_clients.discard(ws)
        return ws

    async def _on_ws_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text or text in ("welcome", "pong"):
            return
        if text.startswith("message:"):
            payload = text[len("message:"):].strip()
            if self.supervisor is not None and payload:
                await self._dispatch_message(payload)
            return
        # 其它帧（agent:/effect:/…）目前只接收广播，不回显

    # ------------------------------------------------------------------ #
    # API：发消息进 agent
    # ------------------------------------------------------------------ #
    async def _handle_message(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        message = str(body.get("message") or "").strip()
        if not message:
            return web.json_response({"ok": False, "error": "empty message"}, status=400)
        session_id = str(body.get("session_id") or "default")
        if self.supervisor is None:
            return web.json_response(
                {"ok": False, "error": "supervisor not bound"}, status=503)
        reply, ok = await self._dispatch_message(message, session_id)
        return web.json_response({"ok": ok, "reply": reply})

    async def _dispatch_message(self, message: str, session_id: str = "default") -> tuple[str, bool]:
        """把消息送进 supervisor，同步推送 user/ai/effect 到 WS 与 HUD。"""
        self._set_local("user", {"text": message, "ts": self._hm()})
        self._set_local("effect", "processing")
        await self._broadcast("patch", {
            "user": self._local_scene["user"], "effect": "processing",
        })
        reply, ok = await asyncio.to_thread(self._invoke_dispatch, message, session_id)
        self._set_local("ai", {"text": reply, "ts": self._hm()})
        self._set_local("effect", "success" if ok else "error")
        await self._broadcast("patch", {
            "ai": self._local_scene["ai"], "effect": self._local_scene["effect"],
        })
        await asyncio.to_thread(self._hud_apply, "user", message)
        await asyncio.to_thread(self._hud_apply, "effect", "success" if ok else "error")
        await asyncio.to_thread(self._hud_apply, "ai", reply)
        return reply, ok

    def _invoke_dispatch(self, message: str, session_id: str) -> tuple[str, bool]:
        sup = self.supervisor
        if sup is None:
            return "（未绑定 RuntimeSupervisor）", False
        try:
            result = sup.dispatch(message, session_id=session_id)
            messages = result.get("messages") or []
            if not messages:
                return "（没有返回内容）", True
            last = messages[-1]
            content = getattr(last, "content", None)
            if content is None and isinstance(last, dict):
                content = last.get("content")
            return str(content or "（没有返回内容）"), True
        except Exception as exc:  # noqa: BLE001 - 调度失败也要回传可读错误
            return f"（调度失败：{exc}）", False

    # ------------------------------------------------------------------ #
    # API：显式下发 HUD 命令
    # ------------------------------------------------------------------ #
    async def _handle_hud(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        command = str(body.get("command") or "")
        if self.hud is None:
            return web.json_response(
                {"ok": False, "error": "hud not bound", "endpoint": None}, status=503)
        ok = await asyncio.to_thread(self._hud_command, command, body)
        # 无论 Flutter 与否，都把这条命令打平到手机 PWA（SCENE 帧）
        await self._record_command(command, body)
        return web.json_response(
            {"ok": ok, "endpoint": getattr(self.hud, "endpoint", None)})

    def _hud_command(self, command: str, body: dict[str, Any]) -> bool:
        hud = self.hud
        if hud is None:
            return False
        try:
            if command == "wake":
                return bool(hud.wake())
            if command == "hide":
                return bool(hud.hide())
            if command == "agent":
                return bool(hud.switch_agent(str(body.get("agent") or "")))
            if command == "effect":
                return bool(hud.effect(str(body.get("name") or "")))
            if command == "user":
                return bool(hud.show_user_text(str(body.get("text") or "")))
            if command == "ai":
                return bool(hud.show_ai_text(str(body.get("text") or "")))
            if command == "monitor":
                return bool(hud.push_monitor(body.get("data") or {}))
        except Exception:  # noqa: BLE001 - 旁路：HUD 失败不抛错
            return False
        return False

    async def _record_command(self, command: str, body: dict[str, Any]) -> None:
        """把下发的命令同步写进本地 scene 并广播给手机（纯投影）。"""
        patch: dict[str, Any] = {}
        if command == "agent":
            self._set_local("agent", str(body.get("agent") or ""))
            patch["agent"] = self._local_scene["agent"]
        elif command == "effect":
            self._set_local("effect", str(body.get("name") or ""))
            patch["effect"] = self._local_scene["effect"]
        elif command == "user":
            self._set_local("user", {"text": str(body.get("text") or ""), "ts": self._hm()})
            patch["user"] = self._local_scene["user"]
        elif command == "ai":
            self._set_local("ai", {"text": str(body.get("text") or ""), "ts": self._hm()})
            patch["ai"] = self._local_scene["ai"]
        elif command == "monitor":
            patch["monitor"] = body.get("data") or {}
        elif command == "wake":
            patch["effect"] = self._local_scene["effect"]
        if patch:
            await self._broadcast("patch", patch)

    # ------------------------------------------------------------------ #
    # HUD 面（composition to HudChannel，在 to_thread 里跑的同步胶水）
    # ------------------------------------------------------------------ #
    def _hud_apply(self, frame_type: str, value: Any) -> bool:
        hud = self.hud
        if hud is None:
            return False
        try:
            if frame_type == "user":
                return bool(hud.show_user_text(str(value)))
            if frame_type == "ai":
                return bool(hud.show_ai_text(str(value)))
            if frame_type == "effect":
                return bool(hud.effect(str(value)))
            if frame_type == "agent":
                return bool(hud.switch_agent(str(value)))
            if frame_type == "hide":
                return bool(hud.hide())
            if frame_type == "wake":
                return bool(hud.wake())
            if frame_type == "monitor" and isinstance(value, dict):
                return bool(hud.push_monitor(value))
        except Exception:  # noqa: BLE001 - 旁路：HUD 失败不抛错
            return False
        return False

    # ------------------------------------------------------------------ #
    # scene 状态（core 持 scene → UI 纯投影，与 N34 对齐）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _new_scene() -> dict[str, Any]:
        return {
            "version": SCENE_VERSION,
            "agent": "jarvis",
            "effect": "idle",
            "user": {"text": "", "ts": ""},
            "ai": {"text": "", "ts": ""},
            "monitor": {},
            "tool_call": "",
        }

    def _scene_snapshot(self) -> dict[str, Any]:
        if self.hud is not None and hasattr(self.hud, "scene_snapshot"):
            try:
                return dict(self.hud.scene_snapshot())
            except Exception:  # noqa: BLE001
                pass
        return json.loads(json.dumps(self._local_scene, ensure_ascii=False))

    def _scene_get(self, key: str) -> Any:
        if self.hud is not None and hasattr(self.hud, "scene_snapshot"):
            try:
                return dict(self.hud.scene_snapshot()).get(key, self._local_scene.get(key))
            except Exception:  # noqa: BLE001
                pass
        return self._local_scene.get(key)

    def _set_local(self, key: str, value: Any) -> None:
        self._local_scene[key] = value

    @staticmethod
    def _hm() -> str:
        return datetime.now().strftime("%H:%M")

    @staticmethod
    def _frame(frame_type: str, payload: Any) -> str:
        return f"{frame_type}:{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"

    @staticmethod
    def _dump(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))

    # ------------------------------------------------------------------ #
    # 广播（async await 直发；线程安全入口用 broadcast）
    # ------------------------------------------------------------------ #
    async def _broadcast(self, frame_type: str, payload: Any) -> None:
        frame = self._frame(frame_type, payload)
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws_clients):
            try:
                await ws.send_str(frame)
            except Exception:  # noqa: BLE001 - 断开即弃
                dead.append(ws)
        for ws in dead:
            self._ws_clients.discard(ws)

    async def _safe_send(self, ws: web.WebSocketResponse, text: str) -> None:
        try:
            await ws.send_str(text)
        except Exception:  # noqa: BLE001 - 旁路：发送失败不抛错
            pass

    def broadcast(self, frame_type: str, payload: Any) -> int:
        """线程安全广播入口（供 harness 回调等任意线程调用）。"""
        loop = getattr(self._app, "loop", None)
        if loop is None or loop.is_closed():
            return 0
        clients = len(self._ws_clients)
        try:
            asyncio.run_coroutine_threadsafe(
                self._broadcast(frame_type, payload), loop)
        except Exception:  # noqa: BLE001
            return 0
        return clients

    # ------------------------------------------------------------------ #
    # 后台委派完成 → 广播 HUD 帧（旁路增强）
    # ------------------------------------------------------------------ #
    def _on_harness_done(self, info: dict[str, Any]) -> None:
        ok = bool(info.get("success"))
        self.broadcast("patch", {"effect": "success" if ok else "error"})
        status = "完成" if ok else "失败"
        head = str(info.get("task") or "")[:30]
        text = f"[后台任务 · {info.get('agent')}] 「{head}」{status}"
        result = str(info.get("result") or "").strip()
        if result:
            text = f"{text}\n{result[:400]}"
        self.broadcast("patch", {"ai": {"text": text, "ts": self._hm()}})

    # ------------------------------------------------------------------ #
    # TLS 上下文（证书缺失 → 静默退回纯 HTTP）
    # ------------------------------------------------------------------ #
    def _build_ssl_context(self) -> ssl.SSLContext | None:
        if not self.tls:
            return None
        cert = Path(self.cert_path)
        key = Path(self.key_path)
        if not cert.is_file() or not key.is_file():
            logger.warning(
                "缺少 TLS 证书（%s），HTTP 网关退回纯 HTTP（旁路降级，%s:%s）",
                cert, self.host, self.port,
            )
            return None
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
            return ctx
        except (ssl.SSLError, OSError, ValueError) as exc:
            logger.warning("TLS 证书加载失败（%s），退回纯 HTTP", exc)
            return None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def attach(
        self,
        supervisor: Any = None,
        hud: Any = None,
        *,
        hook_harness: bool = True,
    ) -> "HttpGateway":
        """挂上真实幕僚长与 HUD；同一 harness 只注册一次回调。"""
        self.supervisor = supervisor
        self.hud = hud
        if supervisor is not None and hook_harness:
            harness = getattr(supervisor, "agent_harness", None)
            on_done = getattr(harness, "on_done", None)
            if on_done is not None and self._harness_wired is not harness:
                on_done(self._on_harness_done)
                self._harness_wired = harness
        return self

    async def start(self) -> "HttpGateway":
        if not self.enabled:
            logger.info("HTTP 网关未启用（http.enabled=false），跳过。")
            return self
        runner = web.AppRunner(self._app)
        await runner.setup()
        ssl_ctx = self._build_ssl_context()
        scheme = "https" if ssl_ctx else "http"
        self._runner = runner
        self._site = web.TCPSite(runner, self.host, self.port, ssl_context=ssl_ctx)
        await self._site.start()
        host = "localhost" if self.host in ("0.0.0.0", "::") else self.host
        self.url = f"{scheme}://{host}:{self.port}/"
        return self

    async def stop(self) -> None:
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws_clients.clear()
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._runner = None
        self._site = None


def _bootstrap(supervisor: Any, hud: Any, gateway: HttpGateway) -> None:
    """可选：拉起真实 RuntimeSupervisor + HUD 并挂接。"""
    gateway.attach(supervisor=supervisor, hud=hud)


async def _run(gateway: HttpGateway, supervisor: Any = None, hud: Any = None) -> None:
    if supervisor is not None:
        try:
            supervisor.wake_up()
        except Exception as exc:  # noqa: BLE001 - 唤醒失败不阻断网关
            logger.warning("wake_up 失败：%s", exc)
    _bootstrap(supervisor, hud, gateway)
    await gateway.start()
    print(f"[HTTP] 网关已启动：{gateway.url}")
    if gateway.tls and gateway._build_ssl_context() is None:
        print("[HTTP] 注意：当前为纯 HTTP（证书缺失）；手机访问需 HTTP 而非 HTTPS。")
    elif gateway.tls:
        print("[HTTP] 自签证书：手机首次访问需在浏览器信任（或导入 CA）后进入。")
    print("[HTTP] 手机同一局域网访问上述地址；Ctrl+C 退出。")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await gateway.stop()
        if supervisor is not None:
            try:
                supervisor.shutdown()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="chuan-os 局域网 HTTPS + 手机 PWA 网关")
    parser.add_argument(
        "--supervisor", action="store_true",
        help="拉起完整栈（RuntimeSupervisor + HUD + wake_up），否则只起静态 PWA/WS/API",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    gateway = HttpGateway()
    supervisor: Any = None
    hud: Any = None
    if args.supervisor:
        from chuan.channels.hud import HudChannel
        from chuan.runtime_supervisor import RuntimeSupervisor

        supervisor = RuntimeSupervisor()
        hud = HudChannel()
    try:
        asyncio.run(_run(gateway, supervisor=supervisor, hud=hud))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()