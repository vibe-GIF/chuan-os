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
from skills.handlers import gui_memory as gm


@pytest.fixture(autouse=True)
def _isolate_mem_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 GUI 元素记忆库 DB 隔离到 tmp，避免真实 gui_locate/click 污染 data/gui/elements.db。"""
    monkeypatch.setattr(gm, "_DB", tmp_path / "gui_mem_test.db")
    yield


@pytest.fixture(autouse=True)
def _dpi_scaling_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """坐标类测试默认按「已声明 DPI 感知」（物理坐标直通）跑，DPI 换算用专门用例覆盖。

    避免缩放屏上 _click_xy 对现有坐标断言做隐性换算；需要测换算的用例内部再显式 set False。
    """
    monkeypatch.setattr(ga, "_dpi_aware", True)
    yield


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
    assert "支持 Windows" in ga.gui_operate("click", description="发送", window="微信")
    assert "支持 Windows" in ga.gui_locate_visual("发送")


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


# ── 阶段 2 执行器：mode 参数 ─────────────────────────

def test_click_silent_mode_no_foreground_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button", silent_ok=False)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_click("发送", "微信", mode="silent")
    assert "后台静默点击失败" in res and "foreground" in res


def test_type_silent_mode_no_foreground_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("输入框", "Edit", silent_ok=False)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_type("hi", "输入框", "微信", mode="silent")
    assert "后台静默输入失败" in res and "foreground" in res


# ── 阶段 3：gui_operate 闭环 ─────────────────────────

def test_operate_invalid_action() -> None:
    res = ga.gui_operate("fly")
    assert "不支持的 action" in res and "click" in res


def test_operate_requires_target() -> None:
    res = ga.gui_operate("click")
    assert "请提供目标" in res


def test_operate_type_requires_text() -> None:
    res = ga.gui_operate("type", description="输入框", window="微信")
    assert "需提供 text" in res


def test_operate_auto_picks_silent_when_located(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button", silent_ok=True)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    seen: dict = {}
    monkeypatch.setattr(ga, "gui_click", lambda *a, **kw: seen.update(kw) or "已点击")
    res = ga.gui_operate("click", description="发送", window="微信", mode="auto", verify=False)
    assert seen.get("mode") == "silent"
    assert "已点击" in res


def test_operate_foreground_conflict_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_user_idle_seconds", lambda: 2)
    res = ga.gui_operate("click", description="发送", window="微信", mode="foreground", verify=False)
    assert "接管屏幕被拒绝" in res and "人机抢鼠标键盘" in res


def test_operate_foreground_allowed_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_user_idle_seconds", lambda: 120)
    seen: dict = {}
    monkeypatch.setattr(ga, "gui_click", lambda *a, **kw: seen.update(kw) or "已点击")
    res = ga.gui_operate(
        "click", description="发送", window="微信", mode="foreground", x=100, y=200, verify=False
    )
    assert seen.get("mode") == "foreground"
    assert seen.get("x") == 100 and seen.get("y") == 200
    assert "已点击" in res


def test_operate_hotkey_dangerous_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    res = ga.gui_operate("hotkey", keys="ctrl+alt+del")
    assert "安全闸拦截" in res


def test_operate_scroll_auto_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_user_idle_seconds", lambda: None)
    seen: dict = {}
    monkeypatch.setattr(ga, "gui_scroll", lambda *a, **kw: seen.update(kw) or "已滚动")
    res = ga.gui_operate("scroll", x=10, y=10, verify=False)
    assert seen.get("x") == 10
    assert "已滚动" in res


def test_operate_audits_and_verifies(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ctl = _Control("发送", "Button", silent_ok=True)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"X")
    monkeypatch.setattr(ga, "gui_screenshot", lambda path="": f"截图已保存: {shot}（1 字节）。")
    log = tmp_path / "actions.log"
    monkeypatch.setattr(ga, "_ACTION_LOG", log)
    res = ga.gui_operate("click", description="发送", window="微信", mode="silent", verify=True)
    assert "后台静默点击" in res
    assert "留痕" in res
    content = log.read_text(encoding="utf-8")
    assert "click" in content and "before=" in content and "after=" in content


# ── 阶段 4：边界 / 异常路径 ──────────────────────────

def test_clamp_bounds() -> None:
    assert ga._clamp(0, 1, 300) == 1
    assert ga._clamp(9999, 1, 300) == 300
    assert ga._clamp("abc", 1, 300) == 300


def test_scroll_amount_clamp_lower_and_nonnum(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pyautogui(monkeypatch)
    ga.gui_scroll("down", 0, x=5, y=5)
    assert calls["scroll"][0][0] == -1  # 0 → 钳制到 1
    calls["scroll"].clear()
    ga.gui_scroll("down", "abc", x=5, y=5)
    assert calls["scroll"][0][0] == -3  # 非数字 → 默认 3


def test_normalize_hotkey_variants() -> None:
    assert ga._normalize_hotkey("  Ctrl + S ") == "ctrl+s"
    assert ga._normalize_hotkey("Ctrl+Shift+Escape") == "ctrl+shift+escape"
    assert ga._normalize_hotkey("alt + f4") == "alt+f4"
    assert ga._normalize_hotkey("  ") == ""


def test_to_pywinauto_keys_variants() -> None:
    assert ga._to_pywinauto_keys("ctrl+s") == "^S"
    assert ga._to_pywinauto_keys("ctrl+alt+delete") == "^%{DELETE}"
    assert ga._to_pywinauto_keys("win+e") == "{LWIN}E"
    assert ga._to_pywinauto_keys("f5") == "{F5}"


def test_hotkey_blocks_normalized_variants() -> None:
    assert "安全闸拦截" in ga.gui_hotkey("Win + L")
    assert "安全闸拦截" in ga.gui_hotkey("CTRL+ALT+DELETE")


def test_hotkey_window_not_found_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([_Win("微信")]))
    res = ga.gui_hotkey("ctrl+s", "不存在的窗口")
    assert "未找到窗口" in res and "微信" in res


def test_click_negative_index_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button")
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    res = ga.gui_click("发送", "微信", index=-1)
    assert "未找到匹配" in res


def test_operate_no_target_rejected() -> None:
    res = ga.gui_operate("click", verify=False)
    assert "请提供目标" in res


def test_operate_foreground_allowed_when_idle_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_user_idle_seconds", lambda: None)
    seen: dict = {}
    monkeypatch.setattr(ga, "gui_click", lambda *a, **kw: seen.update(kw) or "已点击")
    res = ga.gui_operate(
        "click", description="发送", window="微信", mode="foreground", x=100, y=200, verify=False
    )
    assert seen.get("mode") == "foreground"
    assert seen.get("x") == 100 and seen.get("y") == 200
    assert "已点击" in res


def test_operate_verify_false_no_screenshots_but_audits(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ctl = _Control("发送", "Button", silent_ok=True)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    log = tmp_path / "actions.log"
    monkeypatch.setattr(ga, "_ACTION_LOG", log)
    shots = {"n": 0}

    def boom_shot(path: str = ""):
        shots["n"] += 1
        raise RuntimeError("不应被调用")

    monkeypatch.setattr(ga, "gui_screenshot", boom_shot)
    res = ga.gui_operate("click", description="发送", window="微信", mode="silent", verify=False)
    assert "后台静默点击" in res
    assert shots["n"] == 0  # verify=False 不截图
    assert log.exists()  # 但动作日志常开（留痕与截图分离）
    assert "click" in log.read_text(encoding="utf-8")


def test_operate_audit_failure_does_not_block(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ctl = _Control("发送", "Button", silent_ok=True)
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    monkeypatch.setattr(ga, "gui_screenshot", lambda path="": "截图已保存: x.png（1 字节）。")
    # 把日志路径指向「父路径是普通文件」→ mkdir 必然失败，验证 _audit 吞错不阻断
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    monkeypatch.setattr(ga, "_ACTION_LOG", blocker / "actions.log")
    res = ga.gui_operate("click", description="发送", window="微信", mode="silent", verify=True)
    assert "后台静默点击" in res


# ── 阶段 3 增强：UI-TARS 视觉接管 ─────────────────────

def test_parse_coords_variants() -> None:
    assert ga._parse_coords("500,300") == (500, 300)
    assert ga._parse_coords("中心坐标 (120, 80)") == (120, 80)
    assert ga._parse_coords("坐标为：333，222") == (333, 222)
    assert ga._parse_coords("找不到") is None
    assert ga._parse_coords("") is None


def test_extract_path_variants() -> None:
    assert ga._extract_path("截图已保存: /tmp/x.png（10 字节）。") == "/tmp/x.png"
    assert ga._extract_path("截图失败：boom") == ""
    assert ga._extract_path("") == ""


def test_visual_locate_uitars_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UI_TARS_BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(ga, "_uitars_locate", lambda endpoint, desc: (10, 20))
    vision_called = {"n": 0}

    def fake_vision(*a, **kw):
        vision_called["n"] += 1
        return "坐标 1,1"

    fake = types.ModuleType("handlers.vision_analyze")
    fake.vision_analyze = fake_vision
    monkeypatch.setitem(sys.modules, "handlers.vision_analyze", fake)
    assert ga._visual_locate("发送") == (10, 20)
    assert vision_called["n"] == 0  # UI-TARS 命中不再走 qwen-vl


def test_visual_locate_falls_back_to_qwenvl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_uitars_locate", lambda endpoint, desc: None)
    monkeypatch.setattr(
        ga, "gui_screenshot", lambda path="": "截图已保存: /tmp/x.png（1 字节）。"
    )
    fake = types.ModuleType("handlers.vision_analyze")
    fake.vision_analyze = lambda image_ref="", prompt="": "目标中心：500,300"
    monkeypatch.setitem(sys.modules, "handlers.vision_analyze", fake)
    assert ga._visual_locate("发送") == (500, 300)


def test_visual_locate_no_match_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_uitars_locate", lambda endpoint, desc: None)
    monkeypatch.setattr(
        ga, "gui_screenshot", lambda path="": "截图已保存: /tmp/x.png（1 字节）。"
    )
    fake = types.ModuleType("handlers.vision_analyze")
    fake.vision_analyze = lambda image_ref="", prompt="": "找不到"
    monkeypatch.setitem(sys.modules, "handlers.vision_analyze", fake)
    assert ga._visual_locate("发送") is None


def test_gui_locate_visual_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_visual_locate", lambda desc: (100, 200))
    res = ga.gui_locate_visual("发送")
    assert "视觉定位「发送」成功" in res and "(100,200)" in res


def test_gui_locate_visual_requires_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    res = ga.gui_locate_visual("")
    assert "请提供要定位的元素描述" in res


def test_gui_locate_visual_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_visual_locate", lambda desc: None)
    res = ga.gui_locate_visual("发送")
    assert "视觉定位「发送」失败" in res


def test_operate_visual_takeover_autofills_coords(monkeypatch: pytest.MonkeyPatch) -> None:
    # 控件定位不到 → auto 矩阵转前台 → 视觉接管补坐标
    win = _Win("微信", controls=[_Control("别的东西")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    monkeypatch.setattr(ga, "_user_idle_seconds", lambda: 120)
    monkeypatch.setattr(ga, "_visual_locate", lambda desc: (300, 400))
    seen: dict = {}
    monkeypatch.setattr(ga, "gui_click", lambda *a, **kw: seen.update(kw) or "已点击")
    res = ga.gui_operate("click", description="发送", window="微信", mode="auto", verify=False)
    assert seen.get("mode") == "foreground"
    assert seen.get("x") == 300 and seen.get("y") == 400
    assert "视觉接管定位" in res and "(300,400)" in res


def test_operate_visual_takeover_fail_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    win = _Win("微信", controls=[_Control("别的东西")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    monkeypatch.setattr(ga, "_user_idle_seconds", lambda: 120)
    monkeypatch.setattr(ga, "_visual_locate", lambda desc: None)
    res = ga.gui_operate("click", description="发送", window="微信", mode="auto", verify=False)
    assert "操作中止" in res and "视觉接管也找不到" in res


# ── 阶段 3 增强：GUI 元素记忆库集成（N58） ───────────

def test_locate_saves_memory_on_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button")
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    saved: list = []
    monkeypatch.setattr(ga, "_mem_save", lambda *a: saved.append(a))
    res = ga.gui_locate("发送", "微信")
    assert "已记入元素记忆库" in res
    assert len(saved) == 1
    # (win, window_keyword, description, ctl)
    assert saved[0][1] == "微信" and saved[0][2] == "发送" and saved[0][3] is ctl


def test_locate_miss_uses_memory_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    win = _Win("微信", controls=[_Control("别的东西")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    monkeypatch.setattr(ga, "_mem_hint", lambda win, desc: "记忆命中：曾在 [微信] 定位到「发送」@ (60,35)")
    shots = {"n": 0}

    def boom_shot(path: str = ""):
        shots["n"] += 1
        raise RuntimeError("不应走到视觉兜底")

    monkeypatch.setattr(ga, "gui_screenshot", boom_shot)
    res = ga.gui_locate("发送", "微信")
    assert "记忆命中" in res and "(60,35)" in res
    assert shots["n"] == 0  # 记忆命中后不再走视觉兜底


def test_click_memory_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    win = _Win("微信", controls=[_Control("别的东西")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    monkeypatch.setattr(ga, "_mem_best_coords", lambda win, desc: (100, 200))
    seen: dict = {}
    monkeypatch.setattr(
        ga,
        "_click_with_memory_verify",
        lambda x, y, w, kw, d: seen.update(x=x, y=y, kw=kw, d=d) or "已记忆点击",
    )
    res = ga.gui_click("发送", "微信")
    assert seen["x"] == 100 and seen["y"] == 200
    assert seen["kw"] == "微信" and seen["d"] == "发送"
    assert res == "已记忆点击"


def test_click_memory_fallback_none_returns_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    win = _Win("微信", controls=[_Control("别的东西")])
    monkeypatch.setattr(ga, "_desktop", lambda: _Desktop([win]))
    monkeypatch.setattr(ga, "_mem_best_coords", lambda win, desc: None)
    res = ga.gui_click("发送", "微信")
    assert "未找到匹配" in res


# ── 记忆兜底自愈闭环（N58 增强）──────────────────────

def test_img_dhash_missing_pil_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert ga._img_dhash("whatever.png") is None
    assert ga._img_dhash("") is None


def test_click_effect_changed_l1_uia(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = _Control("发送", "Button")
    win = _Win("微信", controls=[ctl])
    monkeypatch.setattr(ga, "_find_control", lambda w, d: ctl)  # 点击后 UIA 已能定位
    status, _ = ga._click_effect_changed("前", "后", win, "发送")
    assert status == "changed"


def test_click_effect_changed_l2_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_find_control", lambda w, d: None)  # L1 未命中
    monkeypatch.setattr(ga, "_extract_path", lambda m: m)
    monkeypatch.setattr(ga, "_img_dhash", lambda p: 0 if "前" in p else 255)  # 汉明距离 8 > 5
    status, _ = ga._click_effect_changed("前", "后", None, "发送")
    assert status == "changed"


def test_click_effect_changed_l2_same(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_find_control", lambda w, d: None)
    monkeypatch.setattr(ga, "_extract_path", lambda m: m)
    monkeypatch.setattr(ga, "_img_dhash", lambda p: 0)  # 完全一致
    status, _ = ga._click_effect_changed("前", "后", None, "发送")
    assert status == "unchanged"


def test_click_effect_changed_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_find_control", lambda w, d: None)
    monkeypatch.setattr(ga, "_extract_path", lambda m: m)
    monkeypatch.setattr(ga, "_img_dhash", lambda p: None)  # 无 PIL
    status, _ = ga._click_effect_changed("前", "后", None, "发送")
    assert status == "unknown"


def _patch_mem_module(monkeypatch: pytest.MonkeyPatch, verify_fn) -> None:
    """把 handlers.gui_memory 替换为带 gui_mem_verify 的 fake 模块（避免双模块 DB 陷阱）。"""
    fake = types.ModuleType("handlers.gui_memory")
    fake.gui_mem_verify = verify_fn
    monkeypatch.setitem(sys.modules, "handlers.gui_memory", fake)


def test_click_with_memory_verify_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "gui_screenshot", lambda: "shot")
    monkeypatch.setattr(ga, "_click_xy", lambda x, y: f"点({x},{y})")
    monkeypatch.setattr(ga.time, "sleep", lambda s: None)
    monkeypatch.setattr(ga, "_click_effect_changed", lambda b, a, w, d: ("changed", "生效"))
    _patch_mem_module(monkeypatch, lambda app, desc, ok: "reset")
    res = ga._click_with_memory_verify(100, 200, None, "微信", "发送")
    assert "点(100,200)" in res and "点击生效" in res


def test_click_with_memory_verify_unchanged_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "gui_screenshot", lambda: "shot")
    monkeypatch.setattr(ga, "_click_xy", lambda x, y: f"点({x},{y})")
    monkeypatch.setattr(ga.time, "sleep", lambda s: None)
    monkeypatch.setattr(ga, "_click_effect_changed", lambda b, a, w, d: ("unchanged", "几乎未变"))
    _patch_mem_module(monkeypatch, lambda app, desc, ok: "kept")
    res = ga._click_with_memory_verify(100, 200, None, "微信", "发送")
    assert "可能未生效" in res and "累积失效记录" in res


def test_click_with_memory_verify_forgotten_relocates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "gui_screenshot", lambda: "shot")
    monkeypatch.setattr(ga, "_click_xy", lambda x, y: f"点({x},{y})")
    monkeypatch.setattr(ga.time, "sleep", lambda s: None)
    monkeypatch.setattr(ga, "_click_effect_changed", lambda b, a, w, d: ("unchanged", "几乎未变"))
    _patch_mem_module(monkeypatch, lambda app, desc, ok: "forgotten")
    relocated: dict = {}
    monkeypatch.setattr(ga, "_relocate_and_resave", lambda w, kw, d: relocated.update(kw=kw, d=d) or "已重定位")
    res = ga._click_with_memory_verify(100, 200, None, "微信", "发送")
    assert res == "已重定位"
    assert relocated["kw"] == "微信" and relocated["d"] == "发送"


def test_click_with_memory_verify_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "gui_screenshot", lambda: "shot")
    monkeypatch.setattr(ga, "_click_xy", lambda x, y: f"点({x},{y})")
    monkeypatch.setattr(ga.time, "sleep", lambda s: None)
    monkeypatch.setattr(ga, "_click_effect_changed", lambda b, a, w, d: ("unknown", ""))
    res = ga._click_with_memory_verify(100, 200, None, "微信", "发送")
    assert "无法确认点击效果" in res and "保留记忆" in res


def test_relocate_and_resave_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_visual_locate", lambda d: (300, 400))
    fake = types.ModuleType("handlers.gui_memory")
    saved: list = []
    fake.gui_mem_save = lambda **kw: saved.append(kw) or True
    monkeypatch.setitem(sys.modules, "handlers.gui_memory", fake)
    monkeypatch.setattr(ga, "_click_xy", lambda x, y: f"点({x},{y})")
    res = ga._relocate_and_resave(None, "微信", "发送")
    assert "视觉重定位" in res and "@ (300,400)" in res
    assert saved[0]["app"] == "微信" and saved[0]["description"] == "发送" and saved[0]["x"] == 300


def test_relocate_and_resave_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_visual_locate", lambda d: None)
    res = ga._relocate_and_resave(None, "微信", "发送")
    assert "视觉重新定位也失败" in res and "人工重新调教" in res
# ── Windows 高 DPI 坐标一致性（N57/N58 前置修复）────────


def _patch_windll(
    monkeypatch: pytest.MonkeyPatch,
    *,
    context_ret: int = 1,
    dpiaware_ret: int = 1,
    getdpi_ret: int = 144,
    context_raise: Exception | None = None,
    getdpi_raise: Exception | None = None,
) -> None:
    """把 ctypes.windll 替换为可配置 fake（enable_dpi_awareness / dpi_scale 用）。"""
    import ctypes

    def _context(v):
        if context_raise is not None:
            raise context_raise
        return context_ret

    def _getdpi():
        if getdpi_raise is not None:
            raise getdpi_raise
        return getdpi_ret

    fake = SimpleNamespace(
        user32=SimpleNamespace(
            SetProcessDpiAwarenessContext=_context,
            SetProcessDPIAware=lambda: dpiaware_ret,
            GetDpiForSystem=_getdpi,
        ),
    )
    monkeypatch.setattr(ctypes, "windll", fake)


def _patch_registry(
    monkeypatch: pytest.MonkeyPatch, applied_dpi: int = 120, fail: bool = False
) -> None:
    """把 winreg 替换为 fake：QueryValueEx 返回 AppliedDPI；fail=True 时 OpenKey 抛异常。"""

    def _open(*a):
        if fail:
            raise OSError("no registry")
        return ("key",)

    fake = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=_open,
        QueryValueEx=lambda key, name: (applied_dpi, None),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)


def test_enable_dpi_awareness_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Linux")
    assert ga.enable_dpi_awareness() is False


def test_enable_dpi_awareness_win_per_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ga, "_dpi_aware", False)
    _patch_windll(monkeypatch, context_ret=1, dpiaware_ret=1)
    assert ga.enable_dpi_awareness() is True
    assert ga._dpi_aware is True


def test_enable_dpi_awareness_win_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ga, "_dpi_aware", False)
    _patch_windll(monkeypatch, context_ret=0, dpiaware_ret=1)  # 上下文失败 → 旧 API 兜底
    assert ga.enable_dpi_awareness() is True
    assert ga._dpi_aware is True


def test_enable_dpi_awareness_win_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ga, "_dpi_aware", False)
    _patch_windll(monkeypatch, context_ret=0, dpiaware_ret=0)
    assert ga.enable_dpi_awareness() is False
    assert ga._dpi_aware is False


def test_enable_dpi_awareness_shcore_missing_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ga, "_dpi_aware", False)
    _patch_windll(monkeypatch, context_raise=AttributeError("no API"), dpiaware_ret=1)
    assert ga.enable_dpi_awareness() is True


def test_dpi_scale_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Linux")
    assert ga.dpi_scale() == 1.0


def test_dpi_scale_registry_applied_dpi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    _patch_registry(monkeypatch, applied_dpi=120)
    _patch_windll(monkeypatch, getdpi_ret=96)  # GetDpiForSystem 被虚拟化成 96，主源仍是注册表
    assert ga.dpi_scale() == 1.25


def test_dpi_scale_registry_fail_fallback_getdpi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    _patch_registry(monkeypatch, fail=True)  # 注册表读失败
    _patch_windll(monkeypatch, getdpi_ret=144)
    assert ga.dpi_scale() == 1.5


def test_dpi_scale_all_fail_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga.platform, "system", lambda: "Windows")
    _patch_registry(monkeypatch, fail=True)
    _patch_windll(monkeypatch, getdpi_raise=AttributeError("no GetDpiForSystem"))
    assert ga.dpi_scale() == 1.0


def test_click_xy_dpi_scaling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_dpi_aware", False)
    monkeypatch.setattr(ga, "dpi_scale", lambda: 1.5)
    calls = _patch_pyautogui(monkeypatch)
    res = ga._click_xy(150, 300)
    assert calls["click"] == [(100, 200)]  # 150/1.5, 300/1.5
    assert "(150,300)" in res  # 返回值仍展示物理坐标


def test_click_xy_no_scaling_when_dpi_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_dpi_aware", True)
    monkeypatch.setattr(ga, "dpi_scale", lambda: 1.5)  # 即使缩放比≠1 也不换算
    calls = _patch_pyautogui(monkeypatch)
    ga._click_xy(150, 300)
    assert calls["click"] == [(150, 300)]


def test_click_xy_no_scaling_when_scale_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ga, "_dpi_aware", False)
    monkeypatch.setattr(ga, "dpi_scale", lambda: 1.0)
    calls = _patch_pyautogui(monkeypatch)
    ga._click_xy(150, 300)
    assert calls["click"] == [(150, 300)]
