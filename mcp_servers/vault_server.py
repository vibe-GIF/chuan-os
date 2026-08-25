"""共享黑板 MCP Server —— 外来 agent 经 MCP 检索/写入共享黑板。

黑板真相落盘在 data/teams/（如 A.json / B.json / default.json，见 N42 TeamStateWriter）。
本 server 为独立进程（stdio），自包含可跑、不依赖 chuan 包（参考 filesystem_server.py），
外来 agent（agents/ 下 claude_code / opencode 等）经 MCP 直接读写黑板：

- list_vaults()          —— 列出可用黑板/团队
- search_vault(query)    —— 检索共享黑板（读 data/teams/*.json）
- write_vault(key, content) —— 写入黑板（追加/新建），限定 data/teams/ 范围内

写操作安全：团队名白名单清洗（剔除斜杠/点等路径分隔符）+ realpath 前缀校验，
保证任何写入都落在 data/teams/ 之内，防路径穿越。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# 项目根目录（硬编码，防止被运行时 cwd 影响）
PROJECT_ROOT = os.path.realpath(r"D:\Dev\Active\chuan-os")
# 共享黑板落盘目录（N42 TeamStateWriter 同款）
TEAMS_DIR = Path(PROJECT_ROOT) / "data" / "teams"
# 写操作允许的根范围（黑板 + 长期记忆；本 server 暴露的黑板写入落 data/teams/）
_ALLOWED_ROOTS = tuple(
    Path(PROJECT_ROOT) / rel for rel in ("data/teams", "data/memory")
)

# 团队名直接拼文件名有路径注入风险（"../../x"），白名单清洗
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_\-:]")

mcp = FastMCP("vault")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_team_name(name: str) -> str:
    """把团队/黑板名清洗成安全文件名（不含路径分隔符，防穿越）。"""
    cleaned = _SAFE_NAME.sub("_", name.strip())[:80] or "default"
    return cleaned


def _team_file(team: str) -> Path:
    """返回团队黑板文件路径，并做 realpath 前缀校验（双保险防路径穿越）。

    Args:
        team: 团队/黑板名，如 "A" / "research"（不带 .json）。

    Returns:
        TEAMS_DIR 内的绝对路径。

    Raises:
        PermissionError: 解析后超出允许范围。
    """
    safe = _safe_team_name(team)
    candidate = TEAMS_DIR / f"{safe}.json"
    real = Path(os.path.realpath(candidate))
    # 双保险：realpath 后必须仍在 data/teams 或 data/memory 允许范围内
    if not any(
        real == root or str(real).startswith(str(root) + os.sep)
        for root in _ALLOWED_ROOTS
    ):
        raise PermissionError(f"路径超出允许范围，禁止访问: {team}")
    return real


def _load_doc(path: Path) -> dict[str, Any]:
    """读取黑板 JSON；文件缺失/损坏返回空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_doc(path: Path, doc: dict[str, Any]) -> None:
    """原子落盘：先写临时文件再替换，避免半截 JSON 污染黑板。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def _snippet(text: str, query: str, width: int = 120) -> str:
    """在文本里找 query（大小写不敏感），返回命中处附近的片段。"""
    flat = " ".join(str(text).split())
    if not query:
        return flat[:width]
    idx = flat.lower().find(query.lower())
    if idx == -1:
        return flat[:width]
    start = max(0, idx - width // 3)
    end = min(len(flat), idx + len(query) + width - width // 3)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


# ------------------------------------------------------------------ #
# 工具定义
# ------------------------------------------------------------------ #
@mcp.tool()
def list_vaults() -> list[str]:
    """列出共享黑板上所有可用团队/黑板文件。

    共享黑板落盘在 data/teams/（如 A.json / B.json / default.json）。
    每个条目包含团队名、角色、整体状态、更新时间与条目数。

    Returns:
        字符串列表，每条格式 "<团队名>  [role=...] [status=...] [updated=...] [entries=N]"；
        目录为空时返回单元素提示。
    """
    try:
        TEAMS_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(TEAMS_DIR.glob("*.json"))
    except OSError as exc:  # noqa: BLE001
        return [f"错误：无法读取黑板目录 - {exc}"]

    if not files:
        return ["（暂无黑板条目，可先用 write_vault 写入）"]

    rows: list[str] = []
    for path in files:
        doc = _load_doc(path)
        role = doc.get("role", "")
        status = doc.get("status", "")
        updated = doc.get("updated_at", "")
        entries = len(doc.get("notes", [])) or len(doc.get("subtasks", []))
        parts = [path.stem]
        if role:
            parts.append(f"role={role}")
        if status:
            parts.append(f"status={status}")
        if updated:
            parts.append(f"updated={updated}")
        parts.append(f"entries={entries}")
        rows.append("  ".join(parts))
    return rows


@mcp.tool()
def search_vault(query: str, vault: str = "", limit: int = 10) -> str:
    """检索共享黑板，返回匹配团队及命中的任务/笔记片段。

    扫描 data/teams/*.json（role/task/subtasks/notes 字段做关键词匹配）。

    Args:
        query: 要检索的关键词（如 "部署" / "服务器"）。
        vault: 限定检索的团队/黑板名（不带 .json）；留空则检索全部黑板。
        limit: 最大返回命中条数（默认 10）。

    Returns:
        格式化命中文本；无命中时返回提示。
    """
    if not query or not query.strip():
        return "错误：查询关键词不能为空"
    query = query.strip()

    try:
        TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        return f"错误：无法读取黑板目录 - {exc}"

    try:
        targets = [_team_file(vault)] if vault else sorted(TEAMS_DIR.glob("*.json"))
    except PermissionError as exc:
        return f"错误：{exc}"

    hits: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        team = path.stem
        doc = _load_doc(path)
        role = doc.get("role", "")
        task = doc.get("task", "")
        notes = doc.get("notes", []) if isinstance(doc.get("notes", []), list) else []
        subtasks = doc.get("subtasks", []) if isinstance(doc.get("subtasks", []), list) else []

        fields: list[str] = []
        for text in (role, task):
            if query.lower() in str(text).lower():
                fields.append(f"task: {_snippet(text, query)}")
        for i, note in enumerate(notes):
            if not isinstance(note, dict):
                continue
            note_text = f"{note.get('key', '')} {note.get('content', '')}"
            if query.lower() in note_text.lower():
                fields.append(f"note#{i}: {_snippet(note_text, query)}")
        for st in subtasks:
            if not isinstance(st, dict):
                continue
            st_text = f"{st.get('id', '')} {st.get('description', '')} {st.get('summary', '')}"
            if query.lower() in st_text.lower():
                fields.append(f"subtask {st.get('id', '?')}: {_snippet(st_text, query)}")
        if not fields:
            continue
        for f in fields[:limit]:
            hits.append(f"[{team}] {f}")

    if not hits:
        return f"没有在黑板上找到与「{query}」相关的命中"
    return "\n".join(hits[: max(1, limit)])


@mcp.tool()
def write_vault(key: str, content: str, team: str = "default") -> str:
    """向共享黑板写入一条笔记（追加到指定团队的 JSON 文件，不存在则新建）。

    写操作限定在 data/teams/ 范围内，团队名做白名单清洗防路径穿越。

    Args:
        key: 笔记标题/键名（如 "部署结论" / "todo-001"）。
        content: 笔记正文。
        team: 目标团队/黑板名（不带 .json）；默认 "default"。

    Returns:
        成功时返回 "写入成功：<团队名>（条目 <N>）"；失败时返回错误信息。
    """
    if not key or not key.strip():
        return "错误：key（笔记标题）不能为空"
    if not content or not content.strip():
        return "错误：content（笔记内容）不能为空"
    key = key.strip()

    try:
        path = _team_file(team)
        TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        return f"错误：{exc}"
    except OSError as exc:  # noqa: BLE001
        return f"错误：无法创建黑板目录 - {exc}"

    doc = _load_doc(path)
    if not doc:
        doc = {"role": "外来 agent", "status": "running", "subtasks": []}
    notes = doc.setdefault("notes", [])
    if not isinstance(notes, list):
        notes = []
        doc["notes"] = notes
    notes.append({"key": key, "content": content, "written_at": _now()})
    doc["updated_at"] = _now()

    try:
        _save_doc(path, doc)
    except OSError as exc:  # noqa: BLE001
        return f"错误：写入失败 - {exc}"
    return f"写入成功：{path.stem}（条目 {len(notes)}）"


# ------------------------------------------------------------------ #
# 入口
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    mcp.run()