"""GUI 元素记忆库 handler —— N58（ADR-055）。

记录「软件 + 控件描述 → 中心坐标 / 控件线索」，让 GUI 自动化越用越不用重新定位：
- ``gui_mem_save``：定位成功后 upsert 一条记忆（同 app+description 更新并命中数 +1）
- ``gui_mem_lookup``：按 app / description 模糊查询（命中数优先排序）
- ``gui_mem_list``：列出记忆（用户可查看「学过什么」）
- ``gui_mem_forget``：删除记忆（软件不再用 / 界面改版后清理）

设计（N58，ADR-055）：
- SQLite 持久化 ``data/gui/elements.db``，表 ``gui_elements``（app, description 唯一）；
  UNIQUE 冲突走 upsert（命中计数 hits 累积，越常用越优先）
- 对齐 blackboard 落盘哲学：定位成功的「物理坐标」沉淀为可复用资产
- 静默降级：DB 打不开 / 读写失败返回空结果，**绝不抛错**（对齐 ADR-007）
- 集成点：gui_locate 命中自动存；gui_click/gui_locate 未命中自动查记忆兜底
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# 项目根目录（解析相对路径）
_ROOT = Path(__file__).resolve().parent.parent.parent
_DB = _ROOT / "data" / "gui" / "elements.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gui_elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    description TEXT NOT NULL,
    window_class TEXT DEFAULT '',
    control_type TEXT DEFAULT '',
    control_text TEXT DEFAULT '',
    uia_hint TEXT DEFAULT '',
    x INTEGER DEFAULT 0,
    y INTEGER DEFAULT 0,
    hits INTEGER DEFAULT 1,
    last_used_at TEXT,
    created_at TEXT,
    UNIQUE(app, description)
);
"""

_FIELDS = ("app", "description", "control_type", "control_text", "x", "y", "hits", "last_used_at")


def _connect() -> sqlite3.Connection:
    """打开元素记忆库（自动建目录 + 建表）。"""
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB), timeout=5)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def gui_mem_save(
    app: str,
    description: str,
    window_class: str = "",
    control_type: str = "",
    control_text: str = "",
    uia_hint: str = "",
    x: int = 0,
    y: int = 0,
) -> bool:
    """记录 / 更新一条元素记忆（upsert，命中数 +1）。失败静默返回 False。"""
    app = (app or "").strip() or "?"
    description = (description or "").strip()
    if not description:
        return False
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO gui_elements
                (app, description, window_class, control_type, control_text, uia_hint, x, y, last_used_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(app, description) DO UPDATE SET
                window_class=excluded.window_class,
                control_type=excluded.control_type,
                control_text=excluded.control_text,
                uia_hint=excluded.uia_hint,
                x=excluded.x,
                y=excluded.y,
                hits=hits+1,
                last_used_at=excluded.last_used_at
            """,
            (app, description, window_class, control_type, control_text, uia_hint, int(x), int(y), now, now),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:  # noqa: BLE001 - 静默降级
        return False


def gui_mem_lookup(app: str = "", description: str = "", top: int = 5) -> list[dict]:
    """查元素记忆：app / description 均模糊匹配；按命中数、最近使用排序。失败返回 []。"""
    try:
        conn = _connect()
        where: list[str] = []
        args: list = []
        if app:
            where.append("app LIKE ?")
            args.append(f"%{app}%")
        if description:
            where.append("description LIKE ?")
            args.append(f"%{description}%")
        sql = f"SELECT {', '.join(_FIELDS)} FROM gui_elements"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY hits DESC, last_used_at DESC LIMIT ?"
        args.append(max(1, min(int(top), 50)))
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(zip(_FIELDS, r)) for r in rows]
    except Exception:  # noqa: BLE001 - 静默降级
        return []


def gui_mem_forget(app: str = "", description: str = "") -> int:
    """删除记忆（app / description 至少给一个）。返回删除条数，失败返回 0。"""
    if not (app or description):
        return 0
    try:
        conn = _connect()
        if app and description:
            cur = conn.execute("DELETE FROM gui_elements WHERE app=? AND description=?", (app, description))
        elif app:
            cur = conn.execute("DELETE FROM gui_elements WHERE app=?", (app,))
        else:
            cur = conn.execute("DELETE FROM gui_elements WHERE description=?", (description,))
        conn.commit()
        n = cur.rowcount or 0
        conn.close()
        return n
    except Exception:  # noqa: BLE001 - 静默降级
        return 0


def gui_mem_list(app: str = "", top: int = 20) -> str:
    """列出记忆（可加 app 过滤），返回可读文本。"""
    rows = gui_mem_lookup(app=app, top=top)
    if not rows:
        return "元素记忆库为空。" + (f"（app 过滤：{app}）" if app else "")
    lines = [f"元素记忆库（{len(rows)} 条）:"] + [
        f"- [{r['app']}]「{r['description']}」→ {r['control_type'] or '?'} "
        f"{r['control_text'] or ''} @ ({r['x']},{r['y']}) ×{r['hits']}（{r['last_used_at']}）"
        for r in rows
    ]
    return "\n".join(lines)
