"""Git 仓库状态采集 handler —— 确定性查看分支/改动/最近提交/stash。

在指定目录（默认项目根）跑只读 git 命令，失败静默降级。
被 skills/git_status.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(repo: Path, *args: str) -> str:
    """跑只读 git 命令，失败（非仓库/无 git/超时）返回空串。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=8,
        )
        return (out.stdout or out.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def git_status(repo: str = "") -> str:
    """采集指定目录（默认项目根）的 git 仓库状态。

    Args:
        repo: 要检查的目录路径；留空默认项目根。

    Returns:
        仓库状态文本；非仓库返回 [ERROR] 提示。
    """
    target = Path(repo).expanduser() if repo.strip() else PROJECT_ROOT
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        return f"[ERROR] 无法解析路径：{repo}"

    if not (target / ".git").exists():
        return f"[ERROR] {target} 不是 git 仓库（无 .git 目录）"

    branch = _git(target, "symbolic-ref", "--short", "-q", "HEAD") or "(detached HEAD)"
    commit = _git(target, "rev-parse", "--short", "HEAD")
    status = _git(target, "status", "--porcelain")

    lines: list = [f"分支: {branch} @ {commit or '?'}"]
    if status:
        modified = [ln for ln in status.splitlines() if ln and not ln.startswith("??")]
        untracked = [ln[3:] for ln in status.splitlines() if ln.startswith("??")]
        lines.append(f"工作区: 有改动（改动 {len(modified)} 个文件，未跟踪 {len(untracked)} 个）")
        for ln in modified[:10]:
            lines.append("  M " + ln)
    else:
        lines.append("工作区: 干净")

    ahead = _git(target, "rev-list", "--count", "@{upstream}...HEAD")
    if ahead.isdigit() and int(ahead) > 0:
        lines.append(f"领先远端: {ahead} 个提交（未推送）")

    stash = _git(target, "stash", "list")
    if stash:
        lines.append(f"stash: {len(stash.splitlines())} 条")

    log = _git(target, "log", "-5", "--oneline")
    if log:
        lines.append("最近提交:")
        for ln in log.splitlines()[:5]:
            lines.append("  " + ln)

    return "\n".join(lines)