"""HTTP 网关（N48 / ADR-043：局域网 HTTPS + 手机 PWA 接入）单元测试。

覆盖：配置解析、静态 PWA 服务、/api/health、SCENE WebSocket（hello/scene/patch）、
/api/message 路由与回复回传、/api/hud 命令下发与广播、TLS 缺失静默降级。
"""
from __future__ import annotations

import json

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from chuan.gateway.http_gateway import HttpGateway


class FakeHud:
    """最小 HudChannel 替身（对齐 scene_snapshot + 高层命令，不真正走 TCP）。"""

    def __init__(self) -> None:
        self.endpoint = "127.0.0.1:17889"
        self.calls: list = []
        self._scene = {
            "version": 1, "agent": "jarvis", "effect": "idle",
            "user": {"text": "", "ts": ""}, "ai": {"text": "", "ts": ""},
            "monitor": {}, "tool_call": "",
        }

    def scene_snapshot(self) -> dict:
        return json.loads(json.dumps(self._scene, ensure_ascii=False))

    def wake(self) -> bool:
        self.calls.append("wake"); return True

    def hide(self) -> bool:
        self.calls.append("hide"); return True

    def switch_agent(self, agent: str) -> bool:
        self.calls.append(("agent", agent)); self._scene["agent"] = agent; return True

    def effect(self, name: str) -> bool:
        self.calls.append(("effect", name)); self._scene["effect"] = name; return True

    def show_user_text(self, text: str) -> bool:
        self.calls.append(("user", text)); self._scene["user"] = {"text": text, "ts": "12:00"}; return True

    def show_ai_text(self, text: str) -> bool:
        self.calls.append(("ai", text)); self._scene["ai"] = {"text": text, "ts": "12:01"}; return True

    def push_monitor(self, data: dict) -> bool:
        self.calls.append(("monitor", data)); return True


class FakeSupervisor:
    """最小 RuntimeSupervisor 替身：dispatch 返回固定回复并记录调用。"""

    def __init__(self, reply: str = "答复") -> None:
        self.reply = reply
        self.calls: list = []
        self.agent_harness = None

    def dispatch(self, message: str, session_id: str = "default") -> dict:
        self.calls.append((message, session_id))
        return {"messages": [{"role": "assistant", "content": self.reply}]}


def _gateway(
    config: dict | None = None,
    *,
    supervisor=None,
    hud=None,
) -> HttpGateway:
    cfg = {"enabled": True, "web_root": "web/"}
    if config:
        cfg.update(config)
    return HttpGateway(config=cfg, supervisor=supervisor, hud=hud)


async def _client(gateway: HttpGateway) -> TestClient:
    client = TestClient(TestServer(gateway._app))
    await client.start_server()
    return client


# --------------------------------------------------------------------- #
# 配置解析
# --------------------------------------------------------------------- #
def test_default_config() -> None:
    g = HttpGateway(config={})
    assert g.port == 8443
    assert g.host == "0.0.0.0"
    assert g.tls is True
    assert g.web_root.name == "web"


def test_load_http_section_from_yaml(tmp_path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "http:\n  enabled: true\n  port: 9000\n  tls: false\n  web_root: web/\n",
        encoding="utf-8",
    )
    g = HttpGateway(config_path=p)
    assert g.port == 9000
    assert g.tls is False


def test_tls_degrades_when_cert_missing() -> None:
    g = _gateway({"tls": True, "cert_path": "data/__missing.pem", "key_path": "data/__missing.pem"})
    assert g._build_ssl_context() is None  # 证书缺失 → 退回纯 HTTP（不抛错）
    g2 = _gateway({"tls": False})
    assert g2._build_ssl_context() is None  # 显式关 TLS


# --------------------------------------------------------------------- #
# 静态 PWA 服务
# --------------------------------------------------------------------- #
async def test_serve_static_assets() -> None:
    g = _gateway()
    client = await _client(g)
    try:
        async with client.get("/") as resp:
            assert resp.status == 200
            assert "川流" in await resp.text()
        async with client.get("/manifest.webmanifest") as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("application/manifest+json")
        async with client.get("/sw.js") as resp:
            assert resp.status == 200
        async with client.get("/app.js") as resp:
            assert resp.status == 200
        async with client.get("/nope-404.png") as resp:
            assert resp.status == 404
    finally:
        await client.close()


async def test_static_path_traversal_guard() -> None:
    g = _gateway()
    resp = await g._serve_file("../pyproject.toml")
    assert resp.status == 404


# --------------------------------------------------------------------- #
# 健康探针
# --------------------------------------------------------------------- #
async def test_health_reports_bindings() -> None:
    g = _gateway(supervisor=FakeSupervisor(), hud=FakeHud())
    client = await _client(g)
    try:
        async with client.get("/api/health") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["supervisor"] is True
            assert data["hud"] is True
    finally:
        await client.close()


# --------------------------------------------------------------------- #
# /api/message 路由
# --------------------------------------------------------------------- #
async def test_api_message_routes_and_replies() -> None:
    sup = FakeSupervisor(reply="收到，先生。")
    g = _gateway(supervisor=sup, hud=FakeHud())
    client = await _client(g)
    try:
        async with client.post("/api/message", json={"message": "在吗", "session_id": "s1"}) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["reply"] == "收到，先生。"
        assert sup.calls == [("在吗", "s1")]
    finally:
        await client.close()


async def test_api_message_requires_supervisor() -> None:
    g = _gateway()
    client = await _client(g)
    try:
        async with client.post("/api/message", json={"message": "hi"}) as resp:
            assert resp.status == 503
    finally:
        await client.close()


# --------------------------------------------------------------------- #
# /api/hud 命令下发
# --------------------------------------------------------------------- #
async def test_api_hud_command_broadcasts_patch() -> None:
    hud = FakeHud()
    g = _gateway(hud=hud)
    client = await _client(g)
    try:
        async with client.ws_connect("/ws") as ws:
            await ws.receive_str()  # hello
            await ws.receive_str()  # scene
            async with client.post("/api/hud", json={"command": "effect", "name": "success"}) as resp:
                assert resp.status == 200
                assert (await resp.json())["ok"] is True
            msg = await ws.receive(timeout=3)
            assert msg.type == WSMsgType.TEXT
            assert "success" in msg.data
        assert ("effect", "success") in hud.calls
    finally:
        await client.close()


async def test_api_hud_requires_hud() -> None:
    g = _gateway()
    client = await _client(g)
    try:
        async with client.post("/api/hud", json={"command": "wake"}) as resp:
            assert resp.status == 503
    finally:
        await client.close()


# --------------------------------------------------------------------- #
# SCENE 协议 WebSocket
# --------------------------------------------------------------------- #
async def test_ws_handshake_hello_and_scene() -> None:
    g = _gateway(hud=FakeHud())
    client = await _client(g)
    try:
        async with client.ws_connect("/ws") as ws:
            hello = json.loads((await ws.receive_str())[6:])
            assert hello["client"] == "chuan-os"
            assert hello["version"] == 1
            scene = json.loads((await ws.receive_str())[6:])
            assert scene["version"] == 1
    finally:
        await client.close()


async def test_ws_message_frame_dispatches_and_pushes() -> None:
    sup = FakeSupervisor(reply="已受理")
    hud = FakeHud()
    g = _gateway(supervisor=sup, hud=hud)
    client = await _client(g)
    try:
        async with client.ws_connect("/ws") as ws:
            await ws.receive_str()  # hello
            await ws.receive_str()  # scene
            await ws.send_str("message:安排一下")
            frames = []
            for _ in range(2):  # patch{user,effect} + patch{ai,effect}
                frames.append((await ws.receive(timeout=5)).data)
            joined = " ".join(frames)
            assert "安排一下" in joined
            assert "已受理" in joined
        assert ("user", "安排一下") in hud.calls
    finally:
        await client.close()


async def test_ws_reconnect_discards_closed_client() -> None:
    hud = FakeHud()
    g = _gateway(hud=hud)
    client = await _client(g)
    try:
        async with client.ws_connect("/ws") as ws:
            await ws.receive_str()  # hello
            await ws.receive_str()  # scene
        # 连接关闭后，再发命令不应报错；_ws_clients 已清理
        assert g._ws_clients == set()
        async with client.post("/api/hud", json={"command": "hide"}) as resp:
            assert resp.status == 200
    finally:
        await client.close()