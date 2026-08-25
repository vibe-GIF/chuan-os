"""HUD 通道 —— 把真实对话推给 Flutter 透明悬浮层（Jarvis 全息人体模型）。

后端（本项目 chuan-os）经 TCP（默认 17889）向 Flutter 前端
（hud_overlay/assistant_overlay.exe）发送命令，指挥 Jarvis 特效：
  - `wake`           触发唤醒动画
  - `hide`           隐藏特效
  - `agent:xxx`      切换到指定角色特效（jarvis / lin-meimei / xiao-nu）
  - `user:{text}`    用户终端区文本
  - `ai:{text}`      AI 终端区文本
  - `effect:xxx`     状态色切换（success / error / processing）
  - `monitor:{json}` 监督者监控快照（左下 SUPERVISOR 面板）

SCENE 协议 v1（借鉴 BaiLongma：core 持 scene → UI 纯投影）：
  - 后端维护一份结构化 ``scene`` 状态（agent/effect/user/ai/monitor/tool_call），
    每次状态变化在发 legacy 命令的同时推 `patch:{json}` 增量帧；
    连接握手发 `hello:{json}`（版本 + caps 能力协商），`scene:{json}` 推全量。
  - 同一协议可被手机 PWA 复用（TCP / 未来 WebSocket 只换传输层）。

薄层原则（ADR-007）：只做「对话 → TCP 命令」的胶水，不自研 UI，
未启动 HUD（端口无监听）时静默降级，不阻断主流程。
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 17889
_READ_TIMEOUT = 1.0
_WRITE_TIMEOUT = 1.0

# SCENE 协议版本（UI 纯投影；前端据此决定如何解析帧）
SCENE_VERSION = 1
# 后端能力清单（hello 握手时告知前端，供能力协商）
SCENE_CAPS = (
    "scene", "patch", "hello", "welcome", "monitor",
    "wake", "hide", "agent", "user", "ai", "effect", "tool_call",
)


class HudChannel:
    """HUD 接入通道：把对话/状态以 TCP 命令推送给 Flutter 悬浮层。

    SCENE 模式（``config`` 的 ``hud.scene``，默认开启）下，每次状态变化
    双发：legacy 命令（旧 Flutter 仍能显示）+ ``patch`` 增量帧（新前端 /
    未来 PWA 复用同一协议）。``hud.scene: false`` 时完全退回 legacy 单发。
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        config_path: str | Path = "config/config.yaml",
    ) -> None:
        if config is None:
            config = self._load_config(config_path).get("hud", {})
        config = config or {}
        self._host = config.get("host", _DEFAULT_HOST)
        self._port = int(config.get("port", _DEFAULT_PORT))
        # SCENE 协议开关（默认开：legacy + patch 双发；false 退回纯 legacy）
        self.scene_enabled = bool(config.get("scene", True))
        # core 持有的 scene 状态（UI 纯投影的数据源）
        self._scene: dict[str, Any] = self._new_scene()

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_config(path: str | Path) -> dict[str, Any]:
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent / p
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def enabled(self) -> bool:
        return True

    @property
    def endpoint(self) -> str:
        return f"{self._host}:{self._port}"

    @property
    def alive(self) -> bool:
        """HUD 前端是否在线（端口是否有监听）。"""
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=_READ_TIMEOUT
            ):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------------ #
    # 发送（TCP 胶水）
    # ------------------------------------------------------------------ #
    def send(self, command: str) -> bool:
        """发送一条命令；HUD 未在线/失败时返回 False（不抛错）。"""
        if not command:
            return False
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=_WRITE_TIMEOUT
            ) as sock:
                sock.sendall(command.encode("utf-8") + b"\n")
                return True
        except OSError:
            return False

    # ------------------------------------------------------------------ #
    # SCENE 状态（core 持 scene → UI 纯投影）
    # ------------------------------------------------------------------ #
    def _new_scene(self) -> dict[str, Any]:
        """初始化一份空白 scene 状态。"""
        return {
            "version": SCENE_VERSION,
            "agent": "jarvis",
            "effect": "idle",
            "user": {"text": "", "ts": ""},
            "ai": {"text": "", "ts": ""},
            "monitor": {},
            "tool_call": "",
        }

    def scene_snapshot(self) -> dict[str, Any]:
        """当前 scene 状态快照（PWA / 调试 / 前端握手后用）。"""
        return json.loads(json.dumps(self._scene, ensure_ascii=False))

    def _set_scene(self, key: str, value: Any) -> bool:
        """更新 scene 单字段；值未变化返回 False（不发 patch）。"""
        if self._scene.get(key) == value:
            return False
        self._scene[key] = value
        return True

    # ------------------------------------------------------------------ #
    # SCENE 协议帧
    # ------------------------------------------------------------------ #
    def send_hello(self) -> bool:
        """握手：告知前端版本与能力（caps 协商）。"""
        payload = {
            "client": "chuan-os",
            "version": SCENE_VERSION,
            "caps": list(SCENE_CAPS),
        }
        return self.send(f"hello:{self._dump(payload)}")

    def send_scene_full(self) -> bool:
        """推送全量 scene 状态（前端初始化 / 重连后用）。"""
        return self.send(f"scene:{self._dump(self._scene)}")

    def send_patch(self, patch: dict[str, Any]) -> bool:
        """推送增量 patch（只含变化字段，UI 纯投影）。"""
        if not patch:
            return False
        return self.send(f"patch:{self._dump(patch)}")

    @staticmethod
    def _dump(obj: Any) -> str:
        """JSON 序列化（含中文原样，前端 jsonDecode 兼容）。"""
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    # ------------------------------------------------------------------ #
    # 高层命令
    # ------------------------------------------------------------------ #
    def wake(self) -> bool:
        return self.send("wake")

    def hide(self) -> bool:
        if self._set_scene("effect", "hide"):
            self.send_patch({"effect": "hide"})
        return self.send("hide")

    def switch_agent(self, agent: str) -> bool:
        changed = self._set_scene("agent", agent)
        if changed and self.scene_enabled:
            self.send_patch({"agent": agent})
        return self.send(f"agent:{agent}")

    def effect(self, name: str) -> bool:
        changed = self._set_scene("effect", name)
        if changed and self.scene_enabled:
            self.send_patch({"effect": name})
        return self.send(f"effect:{name}")

    def show_user_text(self, text: str) -> bool:
        changed = self._set_scene("user", {"text": text, "ts": self._hm()})
        if changed and self.scene_enabled:
            self.send_patch({"user": self._scene["user"]})
        return self.send(f"user:{text}")

    def show_ai_text(self, text: str) -> bool:
        changed = self._set_scene("ai", {"text": text, "ts": self._hm()})
        if changed and self.scene_enabled:
            self.send_patch({"ai": self._scene["ai"]})
        return self.send(f"ai:{text}")

    def push_monitor(self, data: dict[str, Any]) -> bool:
        """推送监督者监控快照到 HUD（`monitor:{json}` 命令 + patch 增量）。"""
        try:
            json_data = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        changed = self._set_scene("monitor", data)
        if changed and self.scene_enabled:
            self.send_patch({"monitor": data})
        return self.send(f"monitor:{json_data}")

    @staticmethod
    def _hm() -> str:
        """当前 HH:mm（对齐前端 _hm() 的终端时间戳）。"""
        from datetime import datetime

        return datetime.now().strftime("%H:%M")


def push_monitor_snapshot(supervisor: Any, hud: HudChannel | None) -> bool:
    """共享推送助手：把监督者 HUD 快照推给 Flutter；无监督者/未启动时静默降级。

    供 CLI（main.py）与语音（voice/main.py）在 dispatch 后调用，保证
    监督者数据在对话驱动的同时实时上屏，不阻塞主流程。
    """
    if hud is None:
        return False
    monitor = getattr(supervisor, "supervisor_monitor", None)
    if monitor is None or not hasattr(monitor, "hud_summary"):
        return False
    return hud.push_monitor(monitor.hud_summary())