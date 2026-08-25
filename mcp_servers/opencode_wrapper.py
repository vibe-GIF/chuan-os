"""OpenCode MCP Wrapper —— 把 OpenCode CLI 包装成 MCP 协议。

幕僚长通过 MCP 调用 OpenCode 的编码能力（写码 / 调试 / 重构 / 审查）。
底层执行 ``opencode run --format json <task>``，从 JSON 事件流提取文本回复。

依赖：本机已安装 OpenCode CLI（https://opencode.ai）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 项目根目录（硬编码，防止被运行时 cwd 影响）
PROJECT_ROOT = Path(r"D:\Dev\Active\chuan-os")

# opencode 可执行文件：优先搜 PATH，找不到再试默认安装位置
_OPENCODE = shutil.which("opencode") or r"C:\Users\JYQ74\.opencode\bin\opencode.exe"

# 编码任务超时（秒）
_TIMEOUT = 600

# 返回给 agent 的最大输出长度（防止刷爆上下文）
_MAX_OUTPUT = 8000

mcp = FastMCP("opencode")


@mcp.tool()
def run_opencode(task: str, dir: str = "") -> str:
    """调用 OpenCode AI 编程助手执行编码任务，适合写代码、调试、重构、代码审查、批量文件修改等复杂编程工作。

    用法示例：
    - run_opencode("在 chuan/tools.py 里加一个 fibonacci 函数并写测试")
    - run_opencode("分析 README.md 的目录结构，总结当前进度")
    - 简单的单文件读写请优先用 read_file / write_file，编码任务才用本工具

    Args:
        task: 任务描述，自然语言即可，越具体越好。
        dir: 工作目录（可选，默认为项目根目录 D:\\Dev\\Active\\chuan-os）。

    Returns:
        OpenCode 的文本回复；失败时返回错误信息。
    """
    if not task or not task.strip():
        return "错误：任务描述不能为空"

    if not Path(_OPENCODE).exists():
        return "错误：opencode 未安装。请先安装 OpenCode CLI（https://opencode.ai）"

    work_dir = dir.strip() or str(PROJECT_ROOT)
    if not Path(work_dir).is_dir():
        return f"错误：工作目录不存在 - {work_dir}"

    try:
        result = subprocess.run(
            [_OPENCODE, "run", "--format", "json", task.strip()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            cwd=work_dir,
            shell=False,
            # stdin 必须隔离：MCP server 的 stdin 是协议管道，
            # opencode 继承后可能读它导致永久挂起
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"错误：OpenCode 执行超时（{_TIMEOUT}s），任务可能太复杂，请拆分后重试"
    except OSError as exc:
        return f"错误：启动 opencode 失败 - {exc}"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "no output").strip()
        return f"[OPENCODE ERROR exit={result.returncode}] {err[:2000]}"

    # 从 JSON 事件行中提取 type == "text" 的回复
    replies: list[str] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "text":
            part = event.get("part") or {}
            text = part.get("text")
            if text:
                replies.append(str(text))

    if not replies:
        return "[OPENCODE COMPLETED] 任务已执行，无文本回复"

    output = "\n".join(replies)
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n…（已截断，原文 {len(output)} 字符）"
    return output


# ------------------------------------------------------------------ #
# 入口
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    mcp.run()
