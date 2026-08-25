"""HUD 悬浮层通道（N16 / Jarvis 全息人体模型）的单元测试。

覆盖：TCP 命令拼接、发送成功/失败降级、alive 探测、配置读取与高层命令。
"""
from __future__ import annotations

import json
import socket
import threading
from chuan.channels.hud import HudChannel, push_monitor_snapshot


class _FakeServer:
    """本地回环 TCP 服务：接收命令，逐个记录发送内容。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))  # 0 → 操作系统分配空闲端口
        self.port = self._sock.getsockname()[1]
        self._sock.listen(8)
        self._closed = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            with conn:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    self.lines.extend(
                        d for d in data.decode("utf-8").strip().split("\n") if d
                    )

    def close(self) -> None:
        self._closed = True
        self._sock.close()


def _server_channel(config: dict | None = None) -> tuple[HudChannel, _FakeServer]:
    server = _FakeServer()
    cfg = {"enabled": True, "host": "127.0.0.1", "port": server.port}
    if config is not None:
        cfg.update(config)
    return HudChannel(config=cfg), server


# --------------------------------------------------------------------- #
# 发送成功 / 降级
# --------------------------------------------------------------------- #
def test_send_delivers_command() -> None:
    channel, server = _server_channel()
    ok = channel.send("wake")
    server.close()
    assert ok is True
    assert server.lines == ["wake"]


def test_send_appends_newline() -> None:
    channel, server = _server_channel()
    ok = channel.send("ai:你好")
    server.close()
    assert ok is True
    assert server.lines == ["ai:你好"]


def test_send_empty_returns_false() -> None:
    channel = HudChannel(config={"host": "127.0.0.1", "port": 1})
    assert channel.send("") is False


def test_send_fails_when_hud_offline() -> None:
    # 端口 1 极小概率被监听；此处关注「连接失败 → 返回 False 且不抛错」
    channel = HudChannel(config={"host": "127.0.0.1", "port": 1})
    assert channel.send("wake") is False


def test_alive_true_when_listening() -> None:
    server = _FakeServer()
    channel = HudChannel(config={"host": "127.0.0.1", "port": server.port})
    assert channel.alive is True
    server.close()


def test_alive_false_when_nothing_listens() -> None:
    channel = HudChannel(config={"host": "127.0.0.1", "port": 1})
    assert channel.alive is False


# --------------------------------------------------------------------- #
# 高层命令拼接（SCENE 模式下 legacy + patch 双发）
# --------------------------------------------------------------------- #
def test_highlevel_commands_build_correct_payload() -> None:
    channel, server = _server_channel()
    channel.wake()
    server.close()
    assert server.lines == ["wake"]


def test_show_text_payloads() -> None:
    channel, server = _server_channel()
    channel.show_user_text("今天天气如何")
    channel.show_ai_text("东北风转西南风，晴")
    channel.effect("success")
    server.close()
    # SCENE 默认开启：legacy 命令 + patch 增量各一条
    assert "user:今天天气如何" in server.lines
    assert "ai:东北风转西南风，晴" in server.lines
    assert "effect:success" in server.lines
    # patch 增量帧（只含变化字段）
    patches = [l for l in server.lines if l.startswith("patch:")]
    assert len(patches) == 3
    assert json.loads(patches[0][6:])["user"]["text"] == "今天天气如何"
    assert json.loads(patches[1][6:])["ai"]["text"] == "东北风转西南风，晴"
    assert json.loads(patches[2][6:]) == {"effect": "success"}


def test_switch_agent_payload() -> None:
    channel, server = _server_channel()
    channel.switch_agent("lin-meimei")
    server.close()
    assert "agent:lin-meimei" in server.lines
    assert json.loads(
        [l for l in server.lines if l.startswith("patch:")][0][6:]
    ) == {"agent": "lin-meimei"}


def test_scene_mode_off_falls_back_to_legacy_only() -> None:
    """hud.scene: false → 完全退回 legacy 单发（无 patch 帧）。"""
    channel, server = _server_channel({"scene": False})
    channel.show_user_text("你好")
    channel.show_ai_text("收到")
    channel.effect("success")
    server.close()
    assert server.lines == ["user:你好", "ai:收到", "effect:success"]
    assert not any(l.startswith("patch:") for l in server.lines)


# --------------------------------------------------------------------- #
# 配置读取
# --------------------------------------------------------------------- #
def test_load_config_reads_hud_section(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "hud:\n"
        "  enabled: true\n"
        "  host: 127.0.0.1\n"
        "  port: 17889\n",
        encoding="utf-8",
    )
    channel = HudChannel(config_path=cfg)
    assert channel.endpoint == "127.0.0.1:17889"


def test_default_port_when_no_config() -> None:
    channel = HudChannel(config={})
    assert channel.endpoint == "127.0.0.1:17889"


# --------------------------------------------------------------------- #
# 监督者监控快照（monitor: 命令 + 共享推送助手）
# --------------------------------------------------------------------- #
def test_push_monitor_sends_json_command() -> None:
    channel, server = _server_channel()
    data = {"stats": {"traces": 1, "active": 0, "dead_ends": 0, "redirects": 0}}
    ok = channel.push_monitor(data)
    server.close()
    assert ok is True
    # legacy monitor 命令 + patch 增量
    monitors = [l for l in server.lines if l.startswith("monitor:")]
    assert len(monitors) == 1
    assert json.loads(monitors[0][8:]) == data
    patch = json.loads(
        [l for l in server.lines if l.startswith("patch:")][0][6:]
    )
    assert patch["monitor"] == data


def test_push_monitor_non_serializable_returns_false() -> None:
    channel = HudChannel(config={"host": "127.0.0.1", "port": 1})
    assert channel.push_monitor({"bad": object()}) is False


# --------------------------------------------------------------------- #
# SCENE 协议 v1：hello 握手 + scene 全量 + patch 增量 + caps 协商
# --------------------------------------------------------------------- #
def test_send_hello_carries_version_and_caps() -> None:
    channel, server = _server_channel()
    ok = channel.send_hello()
    server.close()
    assert ok is True
    assert len(server.lines) == 1
    line = server.lines[0]
    assert line.startswith("hello:")
    hello = json.loads(line[6:])
    assert hello["client"] == "chuan-os"
    assert hello["version"] == 1
    assert "scene" in hello["caps"] and "patch" in hello["caps"]


def test_send_scene_full_dumps_entire_state() -> None:
    channel, server = _server_channel()
    channel.show_user_text("你好")
    channel.effect("processing")
    ok = channel.send_scene_full()
    server.close()
    assert ok is True
    scene_line = [l for l in server.lines if l.startswith("scene:")][0]
    scene = json.loads(scene_line[6:])
    assert scene["version"] == 1
    assert scene["user"]["text"] == "你好"
    assert scene["effect"] == "processing"


def test_scene_snapshot_is_deep_copy_and_updates() -> None:
    channel, _ = _server_channel()
    snap = channel.scene_snapshot()
    assert snap["version"] == 1 and snap["effect"] == "idle"
    # 快照修改不影响内部状态
    snap["effect"] = "hacked"
    assert channel.scene_snapshot()["effect"] == "idle"
    channel.effect("success")
    assert channel.scene_snapshot()["effect"] == "success"


def test_scene_patch_does_not_resend_unchanged_value() -> None:
    channel, server = _server_channel()
    channel.effect("processing")
    channel.effect("processing")  # 值未变 → 不再发 patch
    server.close()
    patches = [l for l in server.lines if l.startswith("patch:")]
    assert len(patches) == 1  # 只有第一条

    # 值变化才发新 patch
    channel2, server2 = _server_channel()
    channel2.effect("processing")
    channel2.effect("success")
    server2.close()
    patches2 = [l for l in server2.lines if l.startswith("patch:")]
    assert len(patches2) == 2


def test_send_patch_empty_returns_false() -> None:
    channel = HudChannel(config={"host": "127.0.0.1", "port": 1})
    assert channel.send_patch({}) is False


def test_send_scene_frames_offline_returns_false() -> None:
    channel = HudChannel(config={"host": "127.0.0.1", "port": 1})
    assert channel.send_hello() is False
    assert channel.send_scene_full() is False
    assert channel.send_patch({"effect": "x"}) is False


class _FakeSupervisor:
    """带 hud_summary 的最小监督者替身（对齐 RuntimeSupervisor.supervisor_monitor）。"""

    def __init__(self, summary: dict) -> None:
        self.supervisor_monitor = _FakeMonitor(summary)


class _FakeMonitor:
    def __init__(self, summary: dict) -> None:
        self._summary = summary

    def hud_summary(self) -> dict:
        return self._summary


def test_push_monitor_snapshot_helper_forwards_summary() -> None:
    channel, server = _server_channel()
    supervisor = _FakeSupervisor({"stats": {"active": 2, "dead_ends": 1, "redirects": 0}})
    ok = push_monitor_snapshot(supervisor, channel)
    server.close()
    assert ok is True
    monitor_line = [l for l in server.lines if l.startswith("monitor:")][0]
    assert monitor_line.startswith("monitor:")
    assert json.loads(monitor_line[8:])["stats"]["active"] == 2


def test_push_monitor_snapshot_degrades_gracefully() -> None:
    # hud 为 None → 静默降级
    assert push_monitor_snapshot(_FakeSupervisor({}), None) is False
    # 监督者没有 hud_summary → 静默降级（不发命令）
    assert push_monitor_snapshot(object(), None) is False

    channel, server = _server_channel()
    try:
        assert push_monitor_snapshot(object(), channel) is False
    finally:
        server.close()
    assert server.lines == []
