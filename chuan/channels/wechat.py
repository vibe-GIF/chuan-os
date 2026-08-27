"""N19 微信接入 —— 幕僚长与微信机器人对接，实现远程操控电脑。

后端选择（ADR-015）：企业微信（WeCom）自建应用。
- 收消息：企业微信回调 JSON（FromUserName / MsgType / Content）→ 交幕僚长路由。
- 发消息：qyapi.weixin.qq.com 的应用消息接口（access_token 自动换取并缓存）。
- 未配置凭据时优雅降级：`handle()` 仍可路由（供本地/测试），`send()` 返回 False。

薄层原则（ADR-007）：只做「收 → 路由 → 发」的胶水，复用 `RuntimeSupervisor.dispatch`，
不自研消息队列、回调加解密等重型能力。

部署约束（ADR-053 W2 决议）：企业微信原生回调是加密 XML（`Encrypt` + AES），本通道
`parse_callback` 只解析**已经中转/网关解包后的明文 JSON**；AES 加解密/原生 XML 解析由
部署时的中转层承担，chuan 侧不实现。
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor

_WECOM_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
_SESSION_PREFIX = "wechat:"


class WeChatChannel:
    """微信接入通道：把微信消息转给幕僚长，把回复送回微信。"""

    def __init__(
        self,
        supervisor: RuntimeSupervisor,
        config: dict[str, Any] | None = None,
        config_path: str | Path = "config/config.yaml",
    ) -> None:
        self._sup = supervisor
        if config is None:
            config = self._load_config(config_path).get("wechat", {})
        self._config = config or {}
        self._access_token: str | None = None
        self._token_expires: float = 0.0

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
        return bool(self._config.get("enabled"))

    @property
    def configured(self) -> bool:
        """是否已配置足够凭据、可真正收发消息。"""
        c = self._config
        return bool(c.get("corp_id") and c.get("corp_secret") and c.get("agent_id"))

    # ------------------------------------------------------------------ #
    # 核心胶水：收 → 路由 → 发
    # ------------------------------------------------------------------ #
    def handle(self, user_id: str, text: str) -> str:
        """把一条微信消息交给幕僚长，返回要回给用户的回复文本。

        纯路由逻辑（不依赖真实微信），会话按 ``user_id`` 隔离（``wechat:<id>``）。
        路由异常（未唤醒 / 超时等）静默降级返回可读文本，绝不阻断微信通道
        （对齐项目「失败静默降级」惯例）。
        """
        text = (text or "").strip()
        if not text:
            return ""
        try:
            result = self._sup.dispatch(text, session_id=self._session_id(user_id))
        except Exception:  # noqa: BLE001 - 路由失败降级，不向远程用户暴露内部细节
            return "（消息处理失败，请稍后再试）"
        messages = result.get("messages", [])
        if not messages:
            return ""
        return str(messages[-1].get("content", ""))

    def receive(self, user_id: str, text: str) -> tuple[str, str]:
        """收消息 → 路由 → 回发；返回 ``(user_id, reply)``。"""
        reply = self.handle(user_id, text)
        if reply:
            self.send(user_id, reply)
        return user_id, reply

    @staticmethod
    def _session_id(user_id: str) -> str:
        return f"{_SESSION_PREFIX}{user_id}"

    # ------------------------------------------------------------------ #
    # 收消息解析（企业微信回调 JSON）
    # ------------------------------------------------------------------ #
    @classmethod
    def parse_callback(cls, payload: dict[str, Any] | str) -> tuple[str, str] | None:
        """从企业微信回调解析出 ``(user_id, text)``；非文本消息/解析失败返回 None。"""
        if isinstance(payload, str):
            try:
                data: dict[str, Any] = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                return None
        elif isinstance(payload, dict):
            data = payload
        else:
            return None

        if data.get("MsgType") != "text":
            return None
        user_id = data.get("FromUserName") or ""
        content = data.get("Content") or ""
        if not user_id:
            return None
        return str(user_id), str(content)

    # ------------------------------------------------------------------ #
    # 发消息（企业微信应用消息接口）
    # ------------------------------------------------------------------ #
    def send(self, user_id: str, text: str) -> bool:
        """经企业微信应用消息接口把 ``text`` 发给 ``user_id``；未配置/失败返回 False。"""
        if not (self.enabled and self.configured):
            return False
        token = self._get_access_token()
        if not token:
            return False
        payload = {
            "touser": user_id,
            "msgtype": "text",
            "agentid": int(self._config["agent_id"]),
            "text": {"content": text},
        }
        url = f"{_WECOM_BASE}/message/send?access_token={token}"
        try:
            resp = self._post_json(url, payload)
        except Exception:  # noqa: BLE001 - 网络失败不阻断答复
            return False
        return resp.get("errcode") == 0

    def _get_access_token(self) -> str | None:
        """换取并缓存 access_token（企业微信有效期 7200s，提前 300s 刷新）。"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = (
            f"{_WECOM_BASE}/gettoken?corpid={self._config['corp_id']}"
            f"&corpsecret={self._config['corp_secret']}"
        )
        try:
            resp = self._post_json(url, None)
        except Exception:  # noqa: BLE001
            return None
        token = resp.get("access_token")
        if not token:
            return None
        self._access_token = token
        self._token_expires = time.time() + int(resp.get("expires_in", 7200)) - 300
        return token

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="GET" if data is None else "POST"
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))