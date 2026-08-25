"""天气查询 MCP Server —— 基于 wttr.in 免费 API 提供城市天气查询。

无需 API Key，调用 https://wttr.in/{city}?format=j1 获取 JSON 数据。
N44 旁路：结果经 chuan Cache（Redis 后端 + 内存兜底）按城市 TTL 缓存，命中免外呼。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

# wttr.in 接口超时（秒）
_TIMEOUT = 10
# 天气缓存 TTL（秒）：命中免外呼；默认走 config cache.default_ttl
_WEATHER_TTL = 600

# N44：进程级默认缓存（未启用/Redis 不可达时 no-op/内存兜底，绝不阻断天气查询）
try:
    from chuan.cache import get_cache

    _cache = get_cache()
except Exception:  # noqa: BLE001 - chuan 不可导入时退化为无缓存
    _cache = None


@mcp.tool()
def get_weather(city: str) -> str:
    """查询指定城市的当前天气，返回温度、天气状况和湿度。

    Args:
        city: 城市名称（支持中文或英文，如 "北京" / "Beijing" / "Shanghai"）。

    Returns:
        简短天气文本，格式示例：
        "北京：晴，温度 25°C，湿度 60%"
        网络失败或城市不存在时返回错误信息字符串。
    """
    if not city or not city.strip():
        return "错误：城市名称不能为空"

    city = city.strip()
    cache_key = f"weather:{city}"
    if _cache is not None:
        cached = _cache.get(cache_key)
        if cached:
            return cached

    encoded_city = urllib.parse.quote(city)
    url = f"https://wttr.in/{encoded_city}?format=j1"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "chuan-os-weather-mcp/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return f"错误：未找到城市「{city}」的天气数据"
        return f"错误：天气服务返回 HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return f"错误：网络连接失败 - {exc.reason}"
    except TimeoutError:
        return "错误：天气服务请求超时"
    except Exception as exc:  # noqa: BLE001
        return f"错误：请求天气服务失败 - {exc}"

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return f"错误：天气服务返回的数据格式异常（城市「{city}」可能不存在）"

    try:
        current = data["current_condition"][0]
        temp_c = current.get("temp_C", "?")
        humidity = current.get("humidity", "?")
        weather_desc_list = current.get("weatherDesc", [])
        weather_desc = weather_desc_list[0]["value"] if weather_desc_list else "未知"

        # 尝试从 nearest_area 获取更规范的城市名
        area_name = city
        try:
            nearest = data.get("nearest_area", [])
            if nearest:
                area_names = nearest[0].get("areaName", [])
                if area_names:
                    area_name = area_names[0].get("value", city)
        except (KeyError, IndexError, TypeError):
            pass

        result = f"{area_name}：{weather_desc}，温度 {temp_c}°C，湿度 {humidity}%"
        # 成功结果才缓存（错误信息是瞬态的，不缓存）
        if _cache is not None:
            _cache.set(cache_key, result, ttl=_WEATHER_TTL)
        return result
    except (KeyError, IndexError, TypeError) as exc:
        return f"错误：天气数据解析失败 - {exc}"


# ------------------------------------------------------------------ #
# 入口
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    mcp.run()
