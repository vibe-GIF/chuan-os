"""GUI 自动化 handler —— N57（借鉴影刀 RPA 能力）。

阶段 1（N57a）截图 + 元素定位：
- ``gui_screenshot``：mss 截主屏保存 PNG（供查看 / 后续视觉分析）
- ``gui_list_windows``：列出已打开顶层窗口（pywinauto UIA，元素定位前置）
- ``gui_locate``：按窗口标题 + 控件描述定位元素，返回控件信息 + 坐标；
  定位不到时自动截图 + 复用 ``vision_analyze`` 视觉兜底

阶段 2（N57b）鼠标键盘 + 元素操作（ADR-054）：
- ``gui_click``：点击元素（pywinauto 后台静默主力：UIA Invoke / client-side click，
  不抢鼠标键盘；失败降级 pyautogui 前台坐标）
- ``gui_type``：输入文本（Edit 控件 ``set_edit_text`` 后台静默直写；否则前台 type_keys）
- ``gui_scroll``：滚轮滚动（pyautogui 前台滚轮，坐标 / 元素 / 当前光标）
- ``gui_hotkey``：发送快捷键（危险组合被安全闸拦截；按窗口或全局 pyautogui）

设计（N57a/b，ADR-054）：
- Windows 优先（pywinauto 仅 Windows）；非 Windows 返回可读降级信息
- 惰性导入 mss/pywinauto/pyautogui，缺依赖 / 失败静默降级，**绝不抛错**
  （对齐既有 handler「静默降级」惯例，ADR-007）
- 阶段 2 默认走「后台静默模式」（UIA 不激活窗口、不抢真实鼠标键盘），
  前台坐标仅兜底——这是 pywinauto 相对 pyautogui 的核心加分项
- 危险快捷键（Ctrl+Alt+Del / Win+L 等）安全闸拦截，呼应 P4 机器绑定安全
- 截图保存到 data/gui/（对齐 data/media/ 惯例）
"""

from __future__ import annotations

import platform
import re
import time
from pathlib import Path

# 项目根目录（解析相对路径）
_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUT_DIR = _ROOT / "data" / "gui"


def _non_windows_msg() -> str:
    return "GUI 自动化目前支持 Windows（pywinauto 依赖 Windows UIA）；当前平台无法使用。"


# ------------------------------------------------------------------ #
# 截图（mss）
# ------------------------------------------------------------------ #
def gui_screenshot(path: str = "") -> str:
    """截取主屏保存为 PNG，返回保存路径；失败静默降级为可读提示。

    Args:
        path: 保存路径（绝对或相对项目根）；留空默认 data/gui/screenshot_<ts>.png
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    try:
        import mss
    except Exception:  # noqa: BLE001 - 缺依赖降级
        return "截图失败：未安装 mss（pip install mss）。"

    if path:
        out = Path(path)
        if not out.is_absolute():
            out = _ROOT / out
    else:
        _DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = _DEFAULT_OUT_DIR / f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"

    try:
        with mss.mss() as sct:
            shot = sct.shot(output=str(out))
        p = Path(shot or out)
        if not p.exists() or p.stat().st_size == 0:
            return f"截图失败：未生成有效文件（{p}）。"
        return f"截图已保存: {p}（{p.stat().st_size} 字节）。如需分析内容，可调用 vision_analyze 查看该图片。"
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"截图失败：{exc}"


# ------------------------------------------------------------------ #
# 窗口枚举（pywinauto UIA）
# ------------------------------------------------------------------ #
def _desktop():
    """惰性加载 pywinauto Desktop（UIA backend）；失败返回 None。"""
    try:
        from pywinauto import Desktop

        return Desktop(backend="uia")
    except Exception:  # noqa: BLE001
        return None


def _window_desc(win) -> str:
    """顶层窗口 → 一行描述：标题（class）。"""
    try:
        title = win.window_text() or "(无标题)"
        cls = win.class_name() or "?"
        return f"{title}（{cls}）"
    except Exception:  # noqa: BLE001
        return "(未知窗口)"


def gui_list_windows() -> str:
    """列出当前可见的顶层窗口，供定位时挑选目标。"""
    if platform.system() != "Windows":
        return _non_windows_msg()
    desktop = _desktop()
    if desktop is None:
        return "列出窗口失败：未安装 pywinauto（pip install pywinauto）。"
    try:
        wins = [w for w in desktop.windows() if w.is_visible()]
    except Exception as exc:  # noqa: BLE001
        return f"列出窗口失败：{exc}"
    if not wins:
        return "当前没有可见窗口。"
    lines = [f"当前可见窗口（{len(wins)}）:"] + [
        f"- [{i}] {_window_desc(w)}" for i, w in enumerate(wins)
    ]
    return "\n".join(lines)


def _find_window(desktop, window: str):
    """按标题关键词找窗口；window 为空返回 None（调用方自行处理）。"""
    if not window:
        return None
    try:
        return desktop.window(title_re=f".*{window}.*")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ #
# 元素定位（pywinauto UIA）+ 视觉兜底
# ------------------------------------------------------------------ #
def _control_desc(ctl) -> str:
    """控件 → 一行描述：文本（class）@ 坐标。"""
    try:
        text = ctl.window_text() or ""
        cls = ctl.friendly_class_name() or ctl.class_name() or "?"
        r = ctl.rectangle()
        cx, cy = (r.left + r.width // 2, r.top + r.height // 2)
        label = text if text else cls
        return f"{label}（{cls}）@ ({cx},{cy})"
    except Exception:  # noqa: BLE001
        return "(未知控件)"


def _locate_controls(win, keyword: str) -> list:
    """在窗口内按文本/类名关键词找控件，返回匹配控件（最多 8 个）。"""
    kw = keyword.strip().lower()
    if not kw:
        return []
    try:
        descendants = win.descendants()
    except Exception:  # noqa: BLE001
        return []
    hits: list = []
    for ctl in descendants:
        try:
            text = (ctl.window_text() or "").lower()
            cls = (ctl.class_name() or "").lower()
            fcls = (ctl.friendly_class_name() or "").lower()
            if kw in text or kw in cls or kw in fcls:
                hits.append(ctl)
                if len(hits) >= 8:
                    break
        except Exception:  # noqa: BLE001 - 单个控件取属性失败跳过
            continue
    return hits


def gui_locate(description: str = "", window: str = "") -> str:
    """按控件描述在窗口里定位元素，返回控件信息 + 坐标。

    Args:
        description: 控件描述（如 "发送按钮" / "搜索框" / "确认"）
        window: 窗口标题关键词；留空则列出窗口供挑选（或要求指定）

    定位不到时自动截图 + 复用 vision_analyze 视觉兜底。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    desktop = _desktop()
    if desktop is None:
        return "定位失败：未安装 pywinauto（pip install pywinauto）。"

    win = _find_window(desktop, window)
    if win is None or not win.exists():
        # 未指定窗口 / 窗口不存在 → 列出窗口，让 agent 明确目标
        listed = gui_list_windows()
        if not description:
            return listed
        return f"未找到窗口「{window or '(空)'}」。\n{listed}\n请指定正确的窗口标题后重试。"

    if not description.strip():
        return f"已定位窗口：{_window_desc(win)}。请提供要定位的控件描述（如按钮/输入框文本）。"

    hits = _locate_controls(win, description)
    if hits:
        lines = [f"在窗口「{_window_desc(win)}」定位到 {len(hits)} 个匹配控件:"] + [
            f"- {_control_desc(c)}" for c in hits
        ]
        return "\n".join(lines)

    # 定位不到 → 视觉兜底：截图 + vision_analyze 分析
    shot = gui_screenshot()
    try:
        from handlers.vision_analyze import vision_analyze

        desc = vision_analyze(shot.split(": ", 1)[1].strip() if ": " in shot else "")
    except Exception:  # noqa: BLE001 - 视觉兜底失败不阻断
        desc = ""
    base = f"在窗口「{_window_desc(win)}」未找到匹配「{description}」的控件（可能是自绘界面或文本不同）。"
    if desc:
        base += f"\n已截图并用视觉分析，屏幕内容：\n{desc}"
    else:
        base += f"\n已截图: {shot}。可先用 gui_screenshot 看屏，再确认控件实际名称。"
    return base


# ------------------------------------------------------------------ #
# 阶段 2（N57b）：元素操作
#   pywinauto 后台静默主力（不抢鼠标键盘）+ pyautogui 前台坐标兜底
# ------------------------------------------------------------------ #
def _find_control(win, description: str, index: int = 0):
    """取第 index 个匹配控件（默认第一个）；无匹配返回 None。"""
    hits = _locate_controls(win, description)
    if 0 <= index < len(hits):
        return hits[index]
    return None


def _resolve_control(desktop, window: str, description: str, index: int = 0):
    """统一解析「窗口 + 控件」。

    Returns:
        (win, ctl, hint): win 可能为 None；ctl 可能为 None；hint 非空表示需要
        直接返回给用户（窗口不存在 / 未指定描述 / 定位不到），调用方 ``return hint``。
    """
    win = _find_window(desktop, window)
    if win is None or not win.exists():
        listed = gui_list_windows()
        return None, None, f"未找到窗口「{window or '(空)'}」。\n{listed}"
    if not description.strip():
        return win, None, f"已定位窗口：{_window_desc(win)}。请提供要操作的控件描述（如按钮/输入框文本）。"
    ctl = _find_control(win, description, index)
    if ctl is None:
        return win, None, (
            f"在窗口「{_window_desc(win)}」未找到匹配「{description}」的控件。"
            f"\n可先调用 gui_locate 定位确认控件实际名称/坐标，或改用 gui_screenshot 看屏。"
        )
    return win, ctl, ""


def _silent_activate(ctl) -> tuple[bool, str]:
    """后台静默激活控件：优先 UIA Invoke 模式，其次 client-side click。不移动鼠标。"""
    for method in ("invoke", "click"):
        fn = getattr(ctl, method, None)
        if not callable(fn):
            continue
        try:
            fn()
            return True, method
        except Exception:  # noqa: BLE001 - 该方法不可用则试下一个
            continue
    return False, ""


def _click_xy(x: int, y: int) -> str:
    """按屏幕坐标真实鼠标点击（pyautogui 前台兜底）。"""
    try:
        import pyautogui
    except Exception:  # noqa: BLE001 - 缺依赖降级
        return "点击失败：未安装 pyautogui（pip install pyautogui）。"
    try:
        pyautogui.click(int(x), int(y))
        return f"已按坐标点击 ({int(x)},{int(y)})（前台真实鼠标）。"
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"点击失败：{exc}"


def gui_click(description: str = "", window: str = "", index: int = 0, x: int = 0, y: int = 0) -> str:
    """点击元素。

    Args:
        description: 控件描述（按钮/输入框文本等）
        window: 窗口标题关键词
        index: 命中多个控件时取第几个（0 起）
        x/y: 前台坐标兜底（不依赖定位）

    默认后台静默（UIA Invoke / client-side click，不抢鼠标键盘）；定位不到控件
    或控件不支持静默激活时，降级为前台真实鼠标（pyautogui / click_input）。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    # 纯坐标路径：无需窗口/控件
    if not description.strip() and (x or y):
        return _click_xy(x, y)
    desktop = _desktop()
    if desktop is None:
        return "点击失败：未安装 pywinauto（pip install pywinauto）。"
    win, ctl, hint = _resolve_control(desktop, window, description, index)
    if ctl is None:
        return hint
    # 后台静默（优先）
    ok, how = _silent_activate(ctl)
    if ok:
        return f"已后台静默点击「{_control_desc(ctl)}」（{how}，未抢焦点）。"
    # 前台兜底：真实鼠标点元素中心
    try:
        r = ctl.rectangle()
        return _click_xy(r.left + r.width // 2, r.top + r.height // 2)
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"点击失败：{exc}"


def gui_type(text: str = "", description: str = "", window: str = "", index: int = 0) -> str:
    """向元素输入文本。

    Args:
        text: 要输入的内容
        description: 控件描述（输入框文本等）
        window: 窗口标题关键词
        index: 命中多个控件时取第几个（0 起）

    Edit 控件走 ``set_edit_text`` 后台静默直写（不抢焦点）；其余控件降级为
    ``set_focus + type_keys`` 前台键盘输入。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    if not text:
        return "输入失败：请提供要输入的内容（text 参数）。"
    desktop = _desktop()
    if desktop is None:
        return "输入失败：未安装 pywinauto（pip install pywinauto）。"
    win, ctl, hint = _resolve_control(desktop, window, description, index)
    if ctl is None:
        return hint
    # 编辑框：后台静默直写（不抢焦点）
    try:
        setter = getattr(ctl, "set_edit_text", None)
        if callable(setter):
            setter(text)
            return f"已后台静默输入到「{_control_desc(ctl)}」（set_edit_text，未抢焦点）。"
    except Exception:  # noqa: BLE001 - 非标准 Edit 则走前台键盘
        pass
    # 前台兜底：激活控件 + type_keys
    try:
        ctl.set_focus()
        ctl.type_keys(text)
        return f"已前台输入到「{_control_desc(ctl)}」（type_keys）。"
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"输入失败：{exc}"


def _clamp_amount(amount: int) -> int:
    try:
        return max(1, min(int(amount), 20))
    except (TypeError, ValueError):
        return 3


def gui_scroll(
    direction: str = "down",
    amount: int = 3,
    x: int = 0,
    y: int = 0,
    window: str = "",
    description: str = "",
    index: int = 0,
) -> str:
    """滚轮滚动。

    Args:
        direction: up/down（或 上/下/向上/向下）
        amount: 滚动格数（1-20）
        x/y: 屏幕坐标；留空则先定位元素悬停其中心，再不行滚当前光标位置
        window/description/index: 定位目标元素（供悬停）

    滚轮为前台真实滚轮（pyautogui），需目标窗口位于该坐标/光标下方。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    try:
        import pyautogui
    except Exception:  # noqa: BLE001 - 缺依赖降级
        return "滚动失败：未安装 pyautogui（pip install pyautogui）。"

    clicks = _clamp_amount(amount)
    sign = -1 if str(direction).lower() in ("down", "下", "向下") else 1

    # 未给坐标但有目标元素 → 定位并悬停到其中心再滚
    if not (x or y) and (description.strip() or window):
        desktop = _desktop()
        if desktop is None:
            return "滚动失败：未安装 pywinauto（pip install pywinauto）。"
        win, ctl, hint = _resolve_control(desktop, window, description, index)
        if ctl is None:
            return hint
        try:
            r = ctl.rectangle()
            x, y = r.left + r.width // 2, r.top + r.height // 2
        except Exception as exc:  # noqa: BLE001 - 静默降级
            return f"滚动失败：{exc}"

    try:
        if x or y:
            pyautogui.scroll(sign * clicks, x, y)
            target = f"坐标 ({x},{y})"
        else:
            pyautogui.scroll(sign * clicks)
            target = "当前鼠标位置"
        return f"已在{target}滚动「{direction}」{clicks} 格（前台滚轮，需窗口位于该处）。"
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"滚动失败：{exc}"


# 安全闸拦截的危险快捷键（ADR-054 安全闸 / 呼应 P4 机器绑定）
_DANGEROUS_HOTKEYS = {"ctrl+alt+del", "ctrl+alt+delete", "win+l", "win+u"}


def _normalize_hotkey(keys: str) -> str:
    """'Ctrl + S' → 'ctrl+s'（小写、按 + 分段、修饰键排前）。"""
    parts = [p.strip().lower() for p in re.split(r"[+ ]+", keys.strip()) if p.strip()]
    mods = {"ctrl", "control", "alt", "shift", "win", "windows"}
    mod, rest = [], []
    for p in parts:
        (mod if p in mods else rest).append(p)
    return "+".join(mod + rest)


def _to_pywinauto_keys(keys: str) -> str:
    """'ctrl+s' → '^s'（pywinauto send_keystrokes 语法）。"""
    mod_map = {
        "ctrl": "^", "control": "^", "alt": "%", "shift": "+",
        "win": "{LWIN}", "windows": "{LWIN}",
    }
    key_map = {
        "enter": "{ENTER}", "esc": "{ESC}", "escape": "{ESC}", "tab": "{TAB}",
        "space": " ", "backspace": "{BACKSPACE}", "delete": "{DELETE}", "del": "{DELETE}",
        "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
        "home": "{HOME}", "end": "{END}", "pgup": "{PGUP}", "pgdn": "{PGDN}",
        "pageup": "{PGUP}", "pagedown": "{PGDN}",
        "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}", "f5": "{F5}",
        "f6": "{F6}", "f7": "{F7}", "f8": "{F8}", "f9": "{F9}", "f10": "{F10}",
        "f11": "{F11}", "f12": "{F12}",
    }
    prefix, main = "", ""
    for p in keys.split("+"):
        if p in mod_map:
            prefix += mod_map[p]
        elif p in key_map:
            main = key_map[p]
        elif len(p) == 1:
            main = p.upper()
        else:
            main = p
    return prefix + main


def gui_hotkey(keys: str = "", window: str = "") -> str:
    """发送快捷键。

    Args:
        keys: 组合键，如 ctrl+s / alt+f4 / win+d / ctrl+shift+esc
        window: 窗口标题关键词；指定则激活该窗口后发送（前台），否则全局发送

    危险组合（Ctrl+Alt+Del / Win+L 等）被安全闸拦截，直接返回提示。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    if not keys.strip():
        return "热键失败：请提供按键组合（如 ctrl+s / alt+f4 / win+d）。"
    norm = _normalize_hotkey(keys)
    if norm in _DANGEROUS_HOTKEYS:
        return f"热键「{keys}」被安全闸拦截（危险序列，ADR-054 安全闸）。如确需执行，请走人工确认。"

    try:
        if window:
            desktop = _desktop()
            if desktop is None:
                return "热键失败：未安装 pywinauto（pip install pywinauto）。"
            win = _find_window(desktop, window)
            if win is None or not win.exists():
                return f"未找到窗口「{window}」。\n" + gui_list_windows()
            win.set_focus()
            win.send_keystrokes(_to_pywinauto_keys(norm))
            return f"已向窗口「{_window_desc(win)}」发送热键 {keys}（前台）。"
        import pyautogui

        pyautogui.hotkey(*norm.split("+"))
        return f"已发送热键 {keys}（前台）。"
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"热键失败：{exc}"
