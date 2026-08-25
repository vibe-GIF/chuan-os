"""内置工具 —— 用普通 @tool 实现，避免 MCP StructuredTool 与模型的兼容性问题。"""

from __future__ import annotations

import os
import urllib.request
import urllib.parse
from pathlib import Path

from langchain_core.tools import tool

PROJECT_ROOT = Path(r"D:\Dev\Active\chuan-os").resolve()


def _resolve(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    real = p.resolve()
    if real != PROJECT_ROOT and not str(real).startswith(str(PROJECT_ROOT) + os.sep):
        raise PermissionError(f"路径超出项目目录: {path}")
    return real


@tool
def read_file(path: str) -> str:
    """读取项目目录内指定文件的文本内容。项目根目录为 D:\\Dev\\Active\\chuan-os。

    示例：read_file("README.md") 读取根目录的 README.md。不确定文件名时先 list_dir(".")。
    """
    try:
        real = _resolve(path)
    except PermissionError as exc:
        return f"错误：{exc}"
    if not real.is_file():
        return f"错误：文件不存在 - {path}"
    try:
        return real.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return real.read_text(encoding="latin-1")
    except Exception as exc:
        return f"错误：读取失败 - {exc}"


@tool
def write_file(path: str, content: str) -> str:
    """向项目目录内指定路径写入文本内容，自动创建父目录。"""
    try:
        real = _resolve(path)
    except PermissionError as exc:
        return f"错误：{exc}"
    try:
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(content, encoding="utf-8")
        return f"写入成功：{real}（{len(content.encode('utf-8'))}字节）"
    except Exception as exc:
        return f"错误：写入失败 - {exc}"


@tool
def list_dir(path: str) -> str:
    """列举项目目录内指定目录的内容。用 '.' 表示项目根目录。"""
    try:
        real = _resolve(path)
    except PermissionError as exc:
        return f"错误：{exc}"
    if not real.is_dir():
        return f"错误：目录不存在 - {path}"
    try:
        lines = []
        for entry in sorted(real.iterdir()):
            if entry.is_dir():
                lines.append(f"DIR  {'-':>10}  {entry.name}")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = "?"
                lines.append(f"FILE {str(size):>10}  {entry.name}")
        return "\n".join(lines) if lines else "(空目录)"
    except Exception as exc:
        return f"错误：列举失败 - {exc}"


@tool
def delete_file(path: str) -> str:
    """删除项目目录内指定文件。"""
    try:
        real = _resolve(path)
    except PermissionError as exc:
        return f"错误：{exc}"
    if not real.is_file():
        return f"错误：文件不存在 - {path}"
    try:
        real.unlink()
        return f"删除成功：{real}"
    except Exception as exc:
        return f"错误：删除失败 - {exc}"


@tool
def get_weather(city: str) -> str:
    """查询指定城市的当前天气，返回温度、天气状况和湿度。如 get_weather('武汉')。"""
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "chuan-os/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
        current = data["current_condition"][0]
        desc = current["weatherDesc"][0]["value"]
        temp = current["temp_C"]
        humidity = current["humidity"]
        return f"{city}：{desc}，温度 {temp}°C，湿度 {humidity}%"
    except Exception as exc:
        return f"天气查询失败：{exc}"


def get_all_tools() -> list:
    return [read_file, write_file, list_dir, delete_file, get_weather]
