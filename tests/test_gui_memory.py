"""GUI 元素记忆库（N58，ADR-055）handler 测试。

覆盖：save/lookup/forget/list 全链路、upsert 命中计数、模糊匹配、
空库/无匹配、DB 打不开的静默降级。DB 指向 tmp_path，不碰真实 data/gui/elements.db。
"""

from __future__ import annotations

import pytest

from skills.handlers import gui_memory as gm


def _patch_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(gm, "_DB", tmp_path / "sub" / "elements.db")


# ── 保存 + 查询 ──────────────────────────────────────

def test_save_and_lookup_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    assert gm.gui_mem_save("微信", "发送按钮", control_type="Button", control_text="发送", x=60, y=35)
    rows = gm.gui_mem_lookup(app="微信", description="发送按钮")
    assert len(rows) == 1
    r = rows[0]
    assert r["app"] == "微信" and r["description"] == "发送按钮"
    assert r["control_type"] == "Button" and r["control_text"] == "发送"
    assert r["x"] == 60 and r["y"] == 35 and r["hits"] == 1


def test_upsert_increments_hits(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    gm.gui_mem_save("微信", "发送按钮", x=60, y=35)
    gm.gui_mem_save("微信", "发送按钮", x=61, y=36)  # 同 app+description → upsert
    rows = gm.gui_mem_lookup(app="微信", description="发送按钮")
    assert len(rows) == 1
    assert rows[0]["hits"] == 2
    assert rows[0]["x"] == 61 and rows[0]["y"] == 36  # 坐标更新为新值


def test_lookup_fuzzy_and_priority(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    gm.gui_mem_save("微信", "发送按钮", x=1, y=1)
    gm.gui_mem_save("微信", "发送按钮", x=2, y=2)  # hits=2
    gm.gui_mem_save("QQ", "发送按钮", x=9, y=9)  # 另一 app
    rows = gm.gui_mem_lookup(description="发送")  # 模糊，跨 app
    assert len(rows) == 2
    assert rows[0]["app"] == "微信"  # hits 高的在前


def test_lookup_no_match_empty(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    assert gm.gui_mem_lookup(app="微信") == []


def test_save_empty_description_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    assert gm.gui_mem_save("微信", "") is False
    assert gm.gui_mem_lookup() == []


# ── 删除 ─────────────────────────────────────────────

def test_forget_by_both(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    gm.gui_mem_save("微信", "发送按钮", x=1, y=1)
    gm.gui_mem_save("微信", "搜索框", x=2, y=2)
    assert gm.gui_mem_forget("微信", "发送按钮") == 1
    assert gm.gui_mem_lookup(app="微信", description="发送按钮") == []
    assert len(gm.gui_mem_lookup(app="微信")) == 1  # 搜索框还在


def test_forget_no_match_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    assert gm.gui_mem_forget("微信", "不存在") == 0


def test_forget_no_args_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    assert gm.gui_mem_forget() == 0


# ── 列表 ─────────────────────────────────────────────

def test_list_renders_readable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    gm.gui_mem_save("微信", "发送按钮", control_type="Button", control_text="发送", x=60, y=35)
    gm.gui_mem_save("微信", "发送按钮", x=60, y=35)  # hits=2
    out = gm.gui_mem_list()
    assert "元素记忆库" in out and "发送按钮" in out and "×2" in out
    assert "微信" in gm.gui_mem_list(app="微信")
    assert "QQ" not in gm.gui_mem_list(app="微信")


def test_list_empty(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_db(monkeypatch, tmp_path)
    assert "为空" in gm.gui_mem_list()


# ── 静默降级 ─────────────────────────────────────────

def test_db_unopenable_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    monkeypatch.setattr(gm, "_DB", blocker / "elements.db")  # 父路径是文件 → mkdir 失败
    assert gm.gui_mem_save("微信", "发送按钮") is False
    assert gm.gui_mem_lookup() == []
    assert gm.gui_mem_forget("微信", "发送按钮") == 0
    assert "为空" in gm.gui_mem_list()
