"""Bash 安全执行 handler —— 受 Guard 防护的命令执行。

被 skills/bash.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。
执行前先过安全检查（危险命令拦截），然后通过 subprocess 执行。

防御层次:
1. LangGraph Guard (post_model_hook) —— 拦截工具调用级危险操作
2. 本模块安全检查 —— 二次兜底，拦截漏网的危险命令模式
3. subprocess timeout —— 防止命令卡死
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

# 项目根目录（用于限制默认工作目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 危险命令模式（与 guard.py 保持一致，handler 层额外兜底）
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf?\s+[/~\\]", "递归删除根目录或主目录"),
    (r":\(\)\{\s*:\|:&\s*\};:", "Fork 炸弹"),
    (r"(?:format|mkfs)\s+\w:", "格式化磁盘"),
    (r"shutdown\s+(?:/[rs]|-h|-P|now)", "关闭/重启系统"),
    (r"dd\s+if=/dev/zero", "破坏设备文件"),
    (r">\s*/dev/(?:null|zero|sd\w)", "覆盖设备文件"),
    (r"(?:DROP\s+DATABASE|DROP\s+TABLE\s+(?!IF\s+EXISTS))", "删除数据库/表"),
    (r"(?:nmap|masscan|nikto)\s+-", "端口/漏洞扫描"),
    (r"(?:curl|wget).*\|\s*(?:bash|sh|python|perl)", "远程代码执行（管道到解释器）"),
]


def _check_safety(command: str) -> str | None:
    """检查命令是否安全，返回错误信息或 None。"""
    for pattern, desc in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return f"[GUARD BLOCKED] 危险命令被拦截：{desc}"
    return None


def run_bash(command: str, timeout: int = 30, cwd: str | None = None) -> str:
    """执行 bash 命令并返回输出。

    通过 subprocess 以 shell=True 执行，支持管道、重定向、环境变量。
    执行前过安全检查，危险命令被拦截不执行。

    Args:
        command: 要执行的命令字符串
        timeout: 超时秒数（默认 30，最大 300）
        cwd: 工作目录（默认项目根目录 D:\\Dev\\Active\\chuan-os）

    Returns:
        命令输出文本；失败时返回错误信息。
    """
    if not command or not command.strip():
        return "[ERROR] 命令不能为空"

    # 安全检查（二次兜底，第一层在 Guard post_model_hook）
    error = _check_safety(command)
    if error:
        return error

    # 限制超时范围
    timeout = max(1, min(timeout, 300))

    work_dir = cwd or str(PROJECT_ROOT)

    try:
        start = time.monotonic()
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=work_dir,
        )
        elapsed = time.monotonic() - start
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] 命令执行超时（{timeout}s）"
    except OSError as exc:
        return f"[ERROR] 命令执行失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"[ERROR] 命令执行异常：{exc}"

    output = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        msg = f"[EXIT {result.returncode}]"
        if stderr:
            return f"{msg} {stderr}"
        if output:
            return f"{msg} {output}"
        return f"{msg} 无输出（耗时 {elapsed:.1f}s）"

    if output:
        return output
    if stderr:
        return stderr
    return f"[COMPLETED] 命令执行成功（耗时 {elapsed:.1f}s）"


# ---------------------------------------------------------------------------
# 独立运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        print(run_bash(cmd))
    else:
        print("用法: python skills/handlers/bash_safe.py <命令>")
        sys.exit(1)