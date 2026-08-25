"""微信接入通道（N19 / ADR-015）的单元测试。

覆盖：企业微信回调解析、会话按 user_id 隔离、路由透传与回复提取、
配置门控（未配置/未启用时 send 优雅降级）。
"""
from __future__ import annotations

from chuan.channels.wechat import WeChatChannel


class _Supervisor:
    def __init__(self, reply: str = "收到"):
        self._reply = reply
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, message: str, *, session_id: str = "default") -> dict:
        self.calls.append((message, session_id))
        return {"messages": [{"role": "assistant", "content": self._reply}]}


# --------------------------------------------------------------------- #
# 回调解析
# --------------------------------------------------------------------- #
def test_parse_callback_text_dict() -> None:
    out = WeChatChannel.parse_callback(
        {"FromUserName": "u1", "MsgType": "text", "Content": "帮我查天气"}
    )
    assert out == ("u1", "帮我查天气")


def test_parse_callback_text_json_string() -> None:
    out = WeChatChannel.parse_callback(
        '{"FromUserName": "u2", "MsgType": "text", "Content": "几点"}'
    )
    assert out == ("u2", "几点")


def test_parse_callback_non_text_returns_none() -> None:
    assert WeChatChannel.parse_callback(
        {"FromUserName": "u1", "MsgType": "image", "Content": ""}
    ) is None


def test_parse_callback_missing_user_returns_none() -> None:
    assert WeChatChannel.parse_callback({"MsgType": "text", "Content": "hi"}) is None


def test_parse_callback_invalid_input_returns_none() -> None:
    assert WeChatChannel.parse_callback("not-json") is None
    assert WeChatChannel.parse_callback(123) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------- #
# 会话隔离与路由透传
# --------------------------------------------------------------------- #
def test_session_id_is_namespaced() -> None:
    assert WeChatChannel._session_id("abc") == "wechat:abc"


def test_handle_routes_and_extracts_reply() -> None:
    sup = _Supervisor(reply="武汉今天 20 度")
    channel = WeChatChannel(sup, config={})
    reply = channel.handle("u1", "武汉天气")
    assert reply == "武汉今天 20 度"
    assert sup.calls == [("武汉天气", "wechat:u1")]


def test_handle_empty_text_returns_empty() -> None:
    sup = _Supervisor()
    channel = WeChatChannel(sup, config={})
    assert channel.handle("u1", "  ") == ""
    assert sup.calls == []


def test_handle_no_messages_returns_empty() -> None:
    class _EmptySupervisor:
        def dispatch(self, message: str, *, session_id: str = "default") -> dict:
            return {"messages": []}

    channel = WeChatChannel(_EmptySupervisor(), config={})  # type: ignore[arg-type]
    assert channel.handle("u1", "hi") == ""


# --------------------------------------------------------------------- #
# 配置门控与发送
# --------------------------------------------------------------------- #
def test_config_properties() -> None:
    configured = WeChatChannel(
        _Supervisor(), config={"enabled": True, "corp_id": "c", "corp_secret": "s", "agent_id": "1"}
    )
    assert configured.enabled is True
    assert configured.configured is True

    partial = WeChatChannel(_Supervisor(), config={"enabled": True, "corp_id": "c"})
    assert partial.configured is False


def test_send_returns_false_when_disabled() -> None:
    channel = WeChatChannel(
        _Supervisor(),
        config={"enabled": False, "corp_id": "c", "corp_secret": "s", "agent_id": "1"},
    )
    assert channel.send("u1", "hi") is False


def test_send_returns_false_when_unconfigured() -> None:
    channel = WeChatChannel(_Supervisor(), config={"enabled": True})
    assert channel.send("u1", "hi") is False


def test_load_config_reads_wechat_section(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "wechat:\n"
        "  enabled: true\n"
        "  backend: wecom\n"
        "  corp_id: corp123\n"
        "  corp_secret: sec\n"
        "  agent_id: 1000002\n",
        encoding="utf-8",
    )
    channel = WeChatChannel(_Supervisor(), config_path=cfg)
    assert channel.enabled is True
    assert channel.configured is True