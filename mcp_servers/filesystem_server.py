"""文件系统 MCP Server —— 提供受限的文件读写与目录列举能力。

所有操作均被限制在项目根目录 D:\\Dev\\Active\\chuan-os 之内，
通过 os.path.realpath 解析后校验前缀，防止路径穿越。
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

# 项目根目录（硬编码，防止被运行时 cwd 影响）
PROJECT_ROOT = os.path.realpath(r"D:\Dev\Active\chuan-os")

mcp = FastMCP("filesystem")


def _resolve_and_check(path: str) -> str:
    """将用户传入路径解析为绝对真实路径，并校验是否在项目根目录内。

    Returns:
        解析后的绝对路径字符串。

    Raises:
        PermissionError: 路径解析后超出项目根目录。
    """
    # 相对路径基于项目根目录解析
    candidate = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
    real = os.path.realpath(candidate)
    # 确保 real 等于 PROJECT_ROOT 或在其子目录下
    if real != PROJECT_ROOT and not real.startswith(PROJECT_ROOT + os.sep):
        raise PermissionError(f"路径超出项目目录范围，禁止访问: {path}")
    return real


# ------------------------------------------------------------------ #
# 工具定义
# ------------------------------------------------------------------ #
@mcp.tool()
def read_file(path: str) -> str:
    """读取项目目录内指定文件的文本内容。项目根目录为 D:\\Dev\\Active\\chuan-os，相对路径基于此根目录。

    用法示例：
    - read_file("README.md") 读取项目根目录的 README.md
    - read_file("chuan/main.py") 读取子目录文件
    - 不确定文件名时先调用 list_dir(".") 查看目录

    Args:
        path: 文件路径，相对路径（如 README.md）或绝对路径。

    Returns:
        文件文本内容；文件不存在时返回错误信息。
    """
    try:
        real_path = _resolve_and_check(path)
    except PermissionError as exc:
        return f"错误：{exc}"

    if not os.path.isfile(real_path):
        return f"错误：文件不存在 - {path}"

    try:
        with open(real_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        # 非文本文件尝试用 latin-1 兜底，避免崩溃
        try:
            with open(real_path, "r", encoding="latin-1") as f:
                return f.read()
        except Exception as exc:  # noqa: BLE001
            return f"错误：读取文件失败 - {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"错误：读取文件失败 - {exc}"


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """向项目目录内指定路径写入文本内容，自动创建父目录。

    Args:
        path: 文件路径，绝对路径或相对于项目根目录的路径。
        content: 要写入的文本内容。

    Returns:
        成功时返回 "写入成功：<路径>（<N>字节）"；失败时返回错误信息。
    """
    try:
        real_path = _resolve_and_check(path)
    except PermissionError as exc:
        return f"错误：{exc}"

    try:
        os.makedirs(os.path.dirname(real_path), exist_ok=True)
        with open(real_path, "w", encoding="utf-8") as f:
            f.write(content)
        byte_count = len(content.encode("utf-8"))
        return f"写入成功：{real_path}（{byte_count}字节）"
    except Exception as exc:  # noqa: BLE001
        return f"错误：写入文件失败 - {exc}"


@mcp.tool()
def list_dir(path: str) -> list[str]:
    """列举项目目录内指定目录的内容，返回文件名、大小和类型。

    Args:
        path: 目录路径，绝对路径或相对于项目根目录的路径。

    Returns:
        字符串列表，每条格式为 "<类型>  <大小>  <名称>"；
        目录不存在或无权限时返回包含错误信息的单元素列表。
    """
    try:
        real_path = _resolve_and_check(path)
    except PermissionError as exc:
        return [f"错误：{exc}"]

    if not os.path.isdir(real_path):
        return [f"错误：目录不存在 - {path}"]

    try:
        entries: list[str] = []
        for name in sorted(os.listdir(real_path)):
            full = os.path.join(real_path, name)
            if os.path.isdir(full):
                entry_type = "DIR "
                size = "-"
            else:
                entry_type = "FILE"
                try:
                    size = str(os.path.getsize(full))
                except OSError:
                    size = "?"
            entries.append(f"{entry_type}  {size:>10}  {name}")
        return entries
    except Exception as exc:  # noqa: BLE001
        return [f"错误：列举目录失败 - {exc}"]


@mcp.tool()
def delete_file(path: str) -> str:
    """删除项目目录内指定文件。

    Args:
        path: 文件路径，绝对路径或相对于项目根目录的路径。

    Returns:
        成功时返回 "删除成功：<路径>"；失败时返回错误信息。
    """
    try:
        real_path = _resolve_and_check(path)
    except PermissionError as exc:
        return f"错误：{exc}"

    if not os.path.isfile(real_path):
        return f"错误：文件不存在 - {path}"

    try:
        os.remove(real_path)
        return f"删除成功：{real_path}"
    except Exception as exc:  # noqa: BLE001
        return f"错误：删除文件失败 - {exc}"


# ------------------------------------------------------------------ #
# 入口
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    mcp.run()
