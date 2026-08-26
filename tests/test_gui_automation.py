"""GUI 自动化（N57，阶段1 截图+定位 / 阶段2 点击+输入+滚动+热键）handler 测试。

覆盖：非 Windows 降级 / 截图成功与降级 / 窗口枚举 / 元素定位（含视觉兜底）/
阶段2 后台静默点击与前台兜底 / 静默输入与前台输入 / 滚动 / 热键（含安全闸拦截）。
pywinauto/mss/pyautogui/vision_analyze 全部 mock，不碰真实屏幕与窗口。
"""

from __future__ import annotations

import builtins
import re
import sys
import types
from types import SimpleNamespace

import pytest

from skills.handlers import gui_automation as ga


# ── 测试替身 ─────────────────────────────────────────

class _Rect:
    left, top, width, height = 10, 20, 100, 30


class _Control:
    def __init__(
        self,
        text: str = "发送",
        cls: str = "Button",
        silent_ok: bool = True,
        edit: bool = False,
    ) -> None:
        self._text = text
        self._cls = cls
        self._silent_ok = silent_ok  # 是否支持后台静默（invoke/click/set_edit_text）
        self._edit = edit
        self.invoked = False
        self.client_clicked = False
        self.click_input_calls = 0
        self.edit_text: str | None = None
        self.typed: str | None = None

    def window_text(self) -> str:
        return self._text

    def friendly_class_name(self) -> str:
        return self._cls

    def class_name(self) -> str:
        return self._cls

    def rectangle(self) -> _Rect:
        return _Rect()

    # 阶段 2 操作替身
    def invoke(self) -> None:
        if not self._silent_ok:
            raise RuntimeError("invoke not supported")
        self.invoked = True

    def click(self) -> None:
        if not self._silent_ok:
            raise RuntimeError("click not supported")
        self.client_clicked = True

    def click_input(self) -> None:
        self.click_input_calls += 1

    def set_edit_text(self, text: str) -> None:
        if not self._silent_ok:
            raise RuntimeError("set_edit_text not supported")
        self.edit_text = text

    def set_focus(self) -> None:
        self._focused = True

    def type_keys(self, text: str) -> None:
        self.typed = text


class _Win:
    def __init__(
        self,
        title: str = "微信",
        cls: str = "WeChatMainWndForPC",
        visible: bool = True,
        controls: list | None = None,
        exists: bool = True,
    ) -> None:
        self._title = title
        self._cls = cls
        self._visible = visible
        self._controls = controls if controls is not None else []
        self._exists = exists

    def window_text(self) -> str:
        return self._title

    def class_name(self) -> str:
        return self._cls

    def is_visible(self) -> bool:
        return self._visible

    def exists(self) -> bool:
        return self._exists

    def descendants(self) -> list:
        return self._controls


class _Desktop:
    def __init__(self, wins: list | None = None) -> None:
        self._wins = wins or [_Win()]

    def windows(self) -> list:
        return self._wins

    def window(self, title_re: str = "") -> _Win:
        pat = re.compile(title_re)
        for w in self._wins:
            if pat.search(w.window_text()):
                return w
        return _Win(title="__none__", exists=False)


class _FakeSCT:
    """模拟 mss 截图上下文（shot 返回保存路径）。"""

    def __init__(self, path: str, fail: bool = False) -> None:
        self._path = path
        self._fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def shot(self, output: str = ""):
        if self._fail:
            raise RuntimeError("screen capture denied")
        return self._path


def _patch_mss(monkeypatch: pytest.MonkeyPatch, sct) -> None:
    """把 sys.modules['mss'] 换成带 mss() 工厂的 fake 模块（handler 函数内 import mss 会命中）。"""
    fake = types.ModuleType("mss")
    fake.mss = lambda: sct
    monkeypatch.setitem(sys.modules, "mss", fake)


def _patch_pyautogui(monkeypatch: pytest.MonkeyPatch, fail_import: bool = False) -> dict:
    """把 sys.modules['pyautogui'] 换成记录调用的 fake；返回 calls 记录。"""
    if fail_import:
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pyautogui":
                raise ImportError("no pyautogui")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        return {}

    fake = types.ModuleType("pyautogui")
    calls = {"click": [], "scroll": [], "hotkey": []}
    fake.click = lambda x=0, y=0, **kw: calls["click"].append((x, y))
    fake.scroll = lambda clicks=0, x=None, y=None, **kw: calls["scroll"].append((clicks, x, y))
    fake.hotkey = lambda *keys, **kw: calls["hotkey"].append(keys)
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return calls


# ── 非 Windows 降级 ──────────────────────────────────

def test_non_windows_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Linux")
    assert "支持 Windows" in ga.gui_screenshot()
    assert "支持 Windows" in ga.gui_list_windows()
    assert "支持 Windows" in ga.gui_locate("发送", "微信")
    assert "支持 Windows" in ga.gui_click("发送", "微信")
    assert "支持 Windows" in ga.gui_type("hi", "输入框", "微信")
    assert "支持 Windows" in ga.gui_scroll()
    assert "支持 Windows" in ga.gui_hotkey("ctrl+s")


# ── 截图 ─────────────────────────────────────────────

def test_screenshot_saves_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    out = tmp_path / "shot.png"
    out.write_bytes(b"PNG")
    _patch_mss(monkeypatch, _FakeSCT(str(out)))
    res = ga.gui_screenshot(str(out))
    assert "截图已保存" in res and str(out) in res


def test_screenshot_missing_dep_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mss":
            raise ImportError("no mss")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    res = ga.gui_screenshot()
    assert "未安装 mss" in res


def test_screenshot_failure_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _patch_mss(monkeypatch, _FakeSCT("", fail=True))
    res = ga.gui_screenshot(str(tmp_path / "x.png"))
    assert "截图失败" in res


# ── 窗口枚举 ─────────────────────────────────────────

def test_list_windows_returns_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    wins = [_Win("微信", visible=True), _Win("记事本", visible=False)]
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop(wins))
    res = ga.gui_list_windows()
    assert "微信" in res
    assert "记事本" not in res  # 只列可见


def test_list_windows_missing_dep_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_desktop", lambda: None)
    res = ga.gui_list_windows()
    assert "未安装 pywinauto" in res


# ── 元素定位 ─────────────────────────────────────────

def test_locate_finds_control(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button")
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_locate("发送", "微信")
    assert "发送" in res and "(60,35)" in res  # 10+100//2, 20+30//2


def test_locate_empty_window_lists_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([_Win("微信")]))
    res = ga.gui_locate("", "")
    assert "微信" in res and "可见窗口" in res


def test_locate_window_not_found_lists_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([_Win("微信")]))
    res = ga.gui_locate("发送", "不存在的窗口")
    assert "未找到窗口" in res and "微信" in res


def test_locate_not_found_falls_back_to_vision(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    win = _Win("微信", controls=[_Control("别的东西", "Edit")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"X")
    monkeypatch.setattr(ga, "gui_screenshot", lambda path="": f"截图已保存: {shot}")

    fake = types.ModuleType("handlers.vision_analyze")
    fake.vision_analyze = lambda image_ref="": "屏幕上有一个发送按钮"
    monkeypatch.setitem(sys.modules, "handlers.vision_analyze", fake)

    res = ga.gui_locate("发送", "微信")
    assert "未找到匹配" in res
    assert "视觉分析" in res and "发送按钮" in res


# ── 阶段 2：点击 ─────────────────────────────────────

def test_click_silent_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button", silent_ok=True)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_click("发送", "微信")
    assert "后台静默点击" in res and "invoke" in res and "未抢焦点" in res
    assert ctl.invoked and not ctl.client_clicked and ctl.click_input_calls == 0


def test_click_silent_falls_back_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button", silent_ok=False)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    calls = _patch_pyautogui(monkeypatch)
    res = ga.gui_click("发送", "微信")
    assert "前台真实鼠标" in res
    assert calls["click"] == [(60, 35)]  # 10+100//2, 20+30//2


def test_click_by_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([_Win("微信")]))
    calls = _patch_pyautogui(monkeypatch)
    res = ga.gui_click("", "", x=100, y=200)
    assert "已按坐标点击" in res and calls["click"] == [(100, 200)]


def test_click_missing_pyautogui_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pyautogui(monkeypatch, fail_import=True)
    res = ga.gui_click("", "", x=100, y=200)
    assert "未安装 pyautogui" in res


def test_click_control_not_found_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    win = _Win("微信", controls=[_Control("别的东西")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_click("发送", "微信")
    assert "未找到匹配" in res and "gui_locate" in res


# ── 阶段 2：输入 ─────────────────────────────────────

def test_type_silent_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("搜索框", "Edit", edit=True)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_type("hello", "搜索框", "微信")
    assert "后台静默输入" in res and "set_edit_text" in res and "未抢焦点" in res
    assert ctl.edit_text == "hello" and ctl.typed is None


def test_type_foreground_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("输入框", "Edit", silent_ok=False)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_type("hello", "输入框", "微信")
    assert "前台输入" in res and "type_keys" in res
    assert ctl.typed == "hello"


def test_type_requires_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([_Win("微信")]))
    res = ga.gui_type("", "搜索框", "微信")
    assert "请提供要输入的内容" in res


# ── 阶段 2：滚动 ─────────────────────────────────────

def test_scroll_by_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pyautogui(monkeypatch)
    res = ga.gui_scroll("down", 3, x=10, y=10)
    assert "滚动" in res and "坐标 (10,10)" in res
    assert calls["scroll"] == [(-3, 10, 10)]


def test_scroll_up_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pyautogui(monkeypatch)
    ga.gui_scroll("up", 5)
    assert calls["scroll"] == [(5, None, None)]


def test_scroll_amount_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pyautogui(monkeypatch)
    ga.gui_scroll("down", 999, x=5, y=5)
    assert calls["scroll"] == [(-20, 5, 5)]


def test_scroll_missing_pyautogui_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pyautogui(monkeypatch, fail_import=True)
    res = ga.gui_scroll("down", 3, x=10, y=10)
    assert "未安装 pyautogui" in res


# ── 阶段 2：热键 ─────────────────────────────────────

def test_hotkey_blocks_dangerous(monkeypatch: pytest.MonkeyPatch) -> None:
    res = ga.gui_hotkey("Ctrl+Alt+Del")
    assert "安全闸拦截" in res
    res2 = ga.gui_hotkey("win+l")
    assert "安全闸拦截" in res2


def test_hotkey_global_pyautogui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pyautogui(monkeypatch)
    res = ga.gui_hotkey("ctrl+s")
    assert "已发送热键" in res and calls["hotkey"] == [("ctrl", "s")]


def test_hotkey_to_window(monkeypatch: pytest.MonkeyPatch) -> None:
    win = _Win("记事本")
    win.set_focus = lambda: setattr(win, "focused", True)
    win.send_keystrokes = lambda keys: setattr(win, "sent", keys)
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_hotkey("ctrl+s", "记事本")
    assert "向窗口" in res and "前台" in res
    assert win.sent == "^S"  # pywinauto 语法


def test_hotkey_requires_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    res = ga.gui_hotkey("")
    assert "请提供按键组合" in res
