"""确定性天气兜底：城市抽取与天气意图识别（无需 LLM / MCP）。

ADR-012 拆分后，天气兜底逻辑归属 Gateway ① MessageRouter。
"""
from chuan.gateway.message_router import MessageRouter


def test_is_weather_intent() -> None:
    assert MessageRouter.is_weather_intent("武汉天气")
    assert MessageRouter.is_weather_intent("今天几度")
    assert MessageRouter.is_weather_intent("明天会不会下雨")
    assert not MessageRouter.is_weather_intent("帮我写个 hello world")


def test_extract_city() -> None:
    assert MessageRouter.extract_city("武汉天气") == "武汉"
    assert MessageRouter.extract_city("武汉今天天气") == "武汉"
    assert MessageRouter.extract_city("武汉的天气怎么样") == "武汉"
    assert MessageRouter.extract_city("北京明天几度") == "北京"
    # 无法抽出城市 → None
    assert MessageRouter.extract_city("从现在到28号的天气") in (None, "")
    assert MessageRouter.extract_city("最近天气怎么样") in (None, "")