"""GUI 自动化 handler —— N57（借鉴影刀 RPA 能力）。

阶段 1（N57a）截图 + 元素定位：
- ``gui_screenshot``：mss 截主屏保存 PNG（供查看 / 后续视觉分析）
- ``gui_list_windows``：列出已打开顶层窗口（pywinauto UIA，元素定位前置）
- ``gui_locate``：按窗口标题 + 控件描述定位元素，返回控件信息 + 坐标；
  定位不到时自动截图 + 复用 ``vision_analyze`` 视觉兜底

阶段 2（N57b）鼠标键盘 + 元素操作：
- ``gui_click``：点击元素（pywinauto 后台静默主力：UIA Invoke / client-side click，
  不抢鼠标键盘；失败降级 pyautogui 前台坐标）
- ``gui_type``：输入文本（Edit 控件 ``set_edit_text`` 后台静默直写；否则前台 type_keys）
- ``gui_scroll``：滚轮滚动（pyautogui 前台滚轮，坐标 / 元素 / 当前光标）
- ``gui_hotkey``：发送快捷键（危险组合被安全闸拦截；按窗口或全局 pyautogui）

阶段 3（N57c）组合闭环 + UI-TARS 视觉接管增强：
- ``gui_operate``：截图 → 定位 → 操作 → 验证截图闭环；双模式并存（后台静默/
  前台接管）+ 动态切换（auto 决策矩阵）+ 静默可见性（留痕）+ 安全闸
- ``gui_locate_visual``：视觉接管定位（pywinauto 定位不到的自绘界面兜底）——
  引擎优先配置的 UI-TARS 端点，兜底复用 qwen-vl 视觉模型返回目标中心坐标

设计（N57a/b/c，ADR-054）：
- Windows 优先（pywinauto 仅 Windows）；非 Windows 返回可读降级信息
- 惰性导入 mss/pywinauto/pyautogui，缺依赖 / 失败静默降级，**绝不抛错**
  （对齐既有 handler「静默降级」惯例，ADR-007）
- 阶段 2 默认走「后台静默模式」（UIA 不激活窗口、不抢真实鼠标键盘），
  前台坐标仅兜底——这是 pywinauto 相对 pyautogui 的核心加分项
- 危险快捷键（Ctrl+Alt+Del / Win+L 等）安全闸拦截，呼应 P4 机器绑定安全
- 截图保存到 data/gui/（对齐 data/media/ 惯例）
"""

from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path

# 项目根目录（解析相对路径）
_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUT_DIR = _ROOT / "data" / "gui"

# 记忆兜底点击后的自愈复核（N58 自愈闭环）——
# 点击 → 延迟复核 → 判定「是否生效」→ 更新记忆置信度 → 连续失效则遗忘 + 视觉重定位
_VERIFY_DELAY = 0.3        # 点击与复核之间的延迟（秒），等弹窗/加载出现
_DHASH_THRESHOLD = 5      # 截图 dHash 差异阈值（64bit 汉明距离，宽松，避免误删好记忆）

# Windows 高 DPI 坐标一致性（N57/N58 前置修复）——
# chuan 主进程默认 DPI-unaware：mss/pywinauto 给物理像素、pyautogui 走逻辑像素，
# 缩放屏（125%/150%）上视觉得到的物理坐标交给 pyautogui 点击会整体偏移一个缩放系数。
# 启动早期声明 DPI 感知统一为物理像素；声明失败时 _click_xy 用 dpi_scale 兜底换算。
_dpi_aware = False  # enable_dpi_awareness 成功时置 True


def enable_dpi_awareness() -> bool:
    """声明进程 DPI 感知（Win10 Per-Monitor V2 → 旧 API 兜底）。成功返回 True。

    只能成功一次，须在进程首次访问 DPI 前调用（放 RuntimeSupervisor 启动早期）。
    失败静默返回 False，调用方以 dpi_scale() 读缩放比在点击时换算兜底，绝不阻断 GUI。
    """
    global _dpi_aware
    if platform.system() != "Windows":
        return False
    try:
        import ctypes

        # Per-Monitor V2（DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4），多屏缩放最准
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            _dpi_aware = True
            return True
    except Exception:  # noqa: BLE001 - 老系统无此 API 则降级
        pass
    try:
        import ctypes

        # 旧 API 兜底：system DPI aware（单屏缩放够用）
        if ctypes.windll.user32.SetProcessDPIAware():
            _dpi_aware = True
            return True
    except Exception:  # noqa: BLE001 - 声明失败静默降级
        pass
    return False


def dpi_scale() -> float:
    """读当前系统 DPI 缩放比（96=100% → 1.0）；非 Windows / 读不到返回 1.0。"""
    if platform.system() != "Windows":
        return 1.0
    try:
        import ctypes

        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:  # noqa: BLE001 - 老系统无 GetDpiForSystem 则走 GDI
        pass
    try:
        import ctypes

        hdc = ctypes.windll.user32.GetDC(None)
        try:
            return ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) / 96.0  # LOGPIXELSX=88
        finally:
            ctypes.windll.user32.ReleaseDC(None, hdc)
    except Exception:  # noqa: BLE001 - 读不到按 100% 处理
        return 1.0


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
        _mem_save(win, window, description, hits[0])  # 记入元素记忆库（失败静默）
        lines.append("（首个匹配已记入元素记忆库，下次可直接命中）")
        return "\n".join(lines)

    # 定位不到 → 先查元素记忆库（越用越不用重新定位）
    mem = _mem_hint(win, description)
    if mem:
        return f"在窗口「{_window_desc(win)}」未找到匹配「{description}」的控件。{mem}"

    # 定位不到且无记忆 → 视觉兜底：截图 + vision_analyze 分析
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
        cx, cy = int(x), int(y)
        # DPI 兜底：若进程仍未声明 DPI 感知（缩放 >100%），pyautogui 走逻辑坐标，
        # 需把物理坐标除以缩放比换算成逻辑坐标，否则会整体偏移一个缩放系数。
        if not _dpi_aware:
            scale = dpi_scale()
            if scale != 1.0:
                cx, cy = int(round(cx / scale)), int(round(cy / scale))
        pyautogui.click(cx, cy)
        return f"已按坐标点击 ({int(x)},{int(y)})（前台真实鼠标）。"
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"点击失败：{exc}"


def gui_click(
    description: str = "",
    window: str = "",
    index: int = 0,
    x: int = 0,
    y: int = 0,
    mode: str = "auto",
) -> str:
    """点击元素。

    Args:
        description: 控件描述（按钮/输入框文本等）
        window: 窗口标题关键词
        index: 命中多个控件时取第几个（0 起）
        x/y: 前台坐标兜底（不依赖定位）
        mode: auto（默认，静默优先→前台兜底）/ silent（仅后台静默，失败即报）/
            foreground（强制前台真实鼠标）——「意图 + 执行器」的选执行器层（N57c）

    默认后台静默（UIA Invoke / client-side click，不抢鼠标键盘）；auto 模式下
    控件不支持静默激活时，降级为前台真实鼠标（pyautogui / click_input）。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    # 纯坐标路径：无需窗口/控件
    if not description.strip() and (x or y):
        if mode == "silent":
            return "静默模式需要控件（description+window），不能只给坐标；可改用 mode=foreground。"
        return _click_xy(x, y)
    desktop = _desktop()
    if desktop is None:
        return "点击失败：未安装 pywinauto（pip install pywinauto）。"
    win, ctl, hint = _resolve_control(desktop, window, description, index)
    if ctl is None:
        # 记忆兜底：UIA 定位不到但记忆库有坐标 → 前台坐标点击 + 自愈复核（N58 闭环）
        mem = _mem_best_coords(win, description)
        if mem:
            return _click_with_memory_verify(mem[0], mem[1], win, window, description)
        return hint
    # 后台静默（silent / auto 优先）
    if mode in ("auto", "silent"):
        ok, how = _silent_activate(ctl)
        if ok:
            return f"已后台静默点击「{_control_desc(ctl)}」（{how}，未抢焦点）。"
        if mode == "silent":
            return f"后台静默点击失败：控件不支持 UIA Invoke/click（{_control_desc(ctl)}）。可改用 mode=foreground 接管屏幕。"
    # 前台（foreground / auto 兜底）：真实鼠标点元素中心
    try:
        r = ctl.rectangle()
        return _click_xy(r.left + r.width // 2, r.top + r.height // 2)
    except Exception as exc:  # noqa: BLE001 - 静默降级
        return f"点击失败：{exc}"


def gui_type(
    text: str = "",
    description: str = "",
    window: str = "",
    index: int = 0,
    mode: str = "auto",
) -> str:
    """向元素输入文本。

    Args:
        text: 要输入的内容
        description: 控件描述（输入框文本等）
        window: 窗口标题关键词
        index: 命中多个控件时取第几个（0 起）
        mode: auto（默认，静默直写→前台键盘兜底）/ silent（仅静默直写）/ foreground（强制前台键盘）

    Edit 控件走 ``set_edit_text`` 后台静默直写（不抢焦点）；auto 模式下其余控件
    降级为 ``set_focus + type_keys`` 前台键盘输入。
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
    # 编辑框：后台静默直写（silent / auto 优先，不抢焦点）
    if mode in ("auto", "silent"):
        try:
            setter = getattr(ctl, "set_edit_text", None)
            if callable(setter):
                setter(text)
                return f"已后台静默输入到「{_control_desc(ctl)}」（set_edit_text，未抢焦点）。"
        except Exception:  # noqa: BLE001 - 非标准 Edit 则走前台键盘
            pass
        if mode == "silent":
            return f"后台静默输入失败：控件非标准 Edit（{_control_desc(ctl)}）。可改用 mode=foreground 接管屏幕。"
    # 前台（foreground / auto 兜底）：激活控件 + type_keys
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


# ------------------------------------------------------------------ #
# 阶段 3（N57c）：gui_operate 组合闭环
#   截图 → 定位 → 操作 → 验证截图；双模式并存 + 动态切换 + 安全闸
# ------------------------------------------------------------------ #
_ACTION_LOG = _DEFAULT_OUT_DIR / "actions.log"
_IDLE_THRESHOLD = 10  # 前台接管需用户空闲 ≥10s，避免人机抢鼠标键盘


def _clamp(val, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return hi


def _user_idle_seconds() -> float | None:
    """系统级用户空闲秒数（GetLastInputInfo）；非 Windows / 失败返回 None。"""
    try:
        import ctypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:  # noqa: BLE001 - 取不到空闲时间按「未知」处理（不拦）
        return None


def _audit(action: str, detail: str, shot_before: str, shot_after: str) -> None:
    """动作留痕：追加一行到 data/gui/actions.log（对齐 blackboard 落盘哲学）。"""
    try:
        _ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _ACTION_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {action} | {detail} | "
                f"before={shot_before} | after={shot_after}\n"
            )
    except Exception:  # noqa: BLE001 - 留痕失败不阻断主流程
        return


def _resolve_operate_mode(action: str, mode: str, description: str, window: str, x: int, y: int):
    """动态切换决策矩阵（N57c）：返回 (最终模式, 提示)。"""
    m = str(mode).strip().lower()
    if m in ("foreground", "前台", "接管", "接管屏幕", "看着", "看着你"):
        return "foreground", ""
    if m in ("silent", "静默", "后台"):
        return "silent", ""
    # auto（默认）：能定位 → 静默；定位不到 → 前台接管；否则停下问用户
    if action in ("scroll", "hotkey"):
        return "foreground", "滚轮/热键天然前台执行。"
    if description.strip() and window:
        desktop = _desktop()
        if desktop is not None:
            win, ctl, _hint = _resolve_control(desktop, window, description, 0)
            if ctl is not None:
                return "silent", ""
        if x or y:
            return "foreground", "定位不到控件，已按给定坐标前台接管。"
        return "foreground", "定位不到控件（可能是自绘界面），需前台接管；若用户正用电脑会先停下确认。"
    if x or y:
        return "foreground", "未提供控件描述，按给定坐标前台接管。"
    return "foreground", "缺少目标（description+window 或 x/y 坐标）。"


def gui_operate(
    action: str = "",
    description: str = "",
    window: str = "",
    text: str = "",
    mode: str = "auto",
    direction: str = "down",
    amount: int = 3,
    keys: str = "",
    x: int = 0,
    y: int = 0,
    verify: bool = True,
    timeout: int = 30,
) -> str:
    """组合操作闭环：截图 → 定位 → 操作 → 验证截图（N57c，ADR-054 阶段 3）。

    Args:
        action: click / type / scroll / hotkey
        description/window: 目标控件（定位）
        text: type 的内容
        mode: auto（默认，决策矩阵自动切换）/ silent（强制后台静默）/
            foreground（强制前台接管屏幕）
        direction/amount: scroll 参数；keys: hotkey 组合
        x/y: 前台坐标兜底
        verify: 操作前后自动截图留痕（默认开）
        timeout: 超时上限（1-300s，钳制）

    双模式并存 + 动态切换 + 静默可见性（留痕）+ 安全闸（危险热键拦截 /
    前台接管先查用户空闲冲突 / 超时钳制）。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    action = str(action).strip().lower()
    valid = {"click", "type", "scroll", "hotkey"}
    if action not in valid:
        return f"操作失败：不支持的 action「{action or '(空)'}」，可选 {sorted(valid)}。"
    if action != "hotkey" and not (description.strip() or window or x or y):
        return "操作失败：请提供目标（控件描述+窗口，或 x/y 坐标）。"
    if action == "type" and not text:
        return "操作失败：type 需提供 text 内容。"
    if action == "hotkey" and not keys:
        return "操作失败：hotkey 需提供 keys 组合。"
    _timeout = _clamp(timeout, 1, 300)

    # 安全闸：危险热键早拦截（governed by ADR-054）
    if action == "hotkey" and _normalize_hotkey(keys) in _DANGEROUS_HOTKEYS:
        return f"操作被安全闸拦截：热键「{keys}」为危险序列，请走人工确认。"

    # 动态切换决策矩阵
    fmode, note = _resolve_operate_mode(action, mode, description, window, x, y)

    # 安全闸：前台接管前冲突检测（避免人机抢鼠标键盘）
    if fmode == "foreground":
        idle = _user_idle_seconds()
        if idle is not None and idle < _IDLE_THRESHOLD:
            return (
                f"接管屏幕被拒绝：检测到用户正活跃（{idle:.0f}s 前有输入），"
                f"避免人机抢鼠标键盘。可稍后再试，或改用 mode=silent 后台静默。"
            )

    # 视觉接管（UI-TARS 增强）：前台无坐标时，用视觉模型补目标中心坐标
    # （pywinauto 定位不到的自绘界面降级链）；找不到则停下问用户
    if fmode == "foreground" and description.strip() and not (x or y):
        vc = _visual_locate(description)
        if vc:
            x, y = vc
            note = (f"{note} " if note else "") + f"视觉接管定位「{description}」@ ({x},{y})。"
        else:
            return (
                f"操作中止：定位不到控件且视觉接管也找不到「{description}」。"
                "请人工确认目标坐标（x/y 参数），或改用 mode=silent 后台静默。"
            )

    # 静默可见性：操作前截图留痕
    shot_before = gui_screenshot() if verify else ""

    if action == "click":
        result = gui_click(description, window, x=x, y=y, mode=fmode)
    elif action == "type":
        result = gui_type(text, description, window, mode=fmode)
    elif action == "scroll":
        result = gui_scroll(direction, amount, x=x, y=y, window=window, description=description)
    else:  # hotkey
        result = gui_hotkey(keys, window=window)

    # 静默可见性：操作后截图留痕 + 动作日志
    shot_after = gui_screenshot() if verify else ""
    _audit(action, f"mode={fmode} timeout={_timeout} target={description or (x, y)} note={note or '-'}", shot_before, shot_after)

    lines = [result]
    if note:
        lines.append(f"提示：{note}")
    if verify:
        lines.append(f"已截图留痕（操作前后），动作日志：{_ACTION_LOG}")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# UI-TARS 视觉接管（N57c 增强，ADR-054 可选引擎）
#   pywinauto 定位不到的自绘界面 → 视觉模型返回目标中心坐标 → 前台执行
#   引擎优先级：配置的 UI-TARS 端点 → qwen-vl（复用 vision_analyze）
# ------------------------------------------------------------------ #
def _load_gui_cfg() -> dict:
    """读取 config.yaml 的 gui 段（如 uitars_url）；读不到返回空 dict。"""
    p = _ROOT / "config" / "config.yaml"
    if not p.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(p.open("r", encoding="utf-8")) or {}
        return data.get("gui") or {}
    except Exception:  # noqa: BLE001 - 配置读不到按未配置处理
        return {}


def _extract_path(msg: str) -> str:
    """从 gui_screenshot 返回文本里解析保存路径；非「截图已保存」开头返回空。"""
    if "截图已保存: " not in msg:
        return ""
    return msg.split("截图已保存: ", 1)[1].split("（", 1)[0].strip()


def _parse_coords(text: str) -> tuple[int, int] | None:
    """从视觉模型返回文本解析中心坐标 'x,y'（允许中文逗号/空格）；失败返回 None。"""
    if not text:
        return None
    m = re.search(r"(\d{1,4})\s*[,，]\s*(\d{1,4})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _uitars_locate(endpoint: str, description: str) -> tuple[int, int] | None:
    """调 UI-TARS 端点定位目标中心坐标（视觉接管引擎，可选增强）。

    契约：POST {endpoint}，JSON {"screenshot": "<截图路径>", "goal": "<描述>"}，
    期望返回 {"x": int, "y": int}（或含 x/y 的 action 对象）。
    超时/非 JSON/缺 x,y → 返回 None（降级到 qwen-vl）。
    """
    try:
        import urllib.request

        shot = gui_screenshot()
        path = _extract_path(shot)
        if not path:
            return None
        payload = __import__("json").dumps({"screenshot": path, "goal": description}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = __import__("json").loads(resp.read().decode("utf-8"))
        if "x" in data and "y" in data:
            return int(data["x"]), int(data["y"])
    except Exception:  # noqa: BLE001 - UI-TARS 不可用/失败静默降级
        return None
    return None


def _visual_locate(description: str) -> tuple[int, int] | None:
    """视觉定位：返回目标元素中心坐标（屏幕坐标系）。

    引擎优先级：
    1. UI-TARS 端点（config gui.uitars_url 或环境变量 UI_TARS_BASE_URL）——可选增强；
    2. qwen-vl 兜底（复用 vision_analyze + 结构化「返回坐标」prompt）。
    任一失败返回 None（静默降级，不抛错）。
    """
    if not description.strip():
        return None
    uitars_url = os.environ.get("UI_TARS_BASE_URL") or _load_gui_cfg().get("uitars_url")
    if uitars_url:
        coords = _uitars_locate(str(uitars_url), description)
        if coords:
            return coords
    # qwen-vl 兜底
    try:
        from handlers.vision_analyze import vision_analyze
    except Exception:  # noqa: BLE001 - 缺依赖降级
        return None
    shot = gui_screenshot()
    path = _extract_path(shot)
    if not path:
        return None
    prompt = (
        f"这是电脑屏幕截图。请在其中找到「{description}」这个元素（按钮/输入框/菜单等）。"
        "只返回它中心的屏幕坐标，格式为两个整数、逗号分隔（例如：500,300）。"
        "如果找不到，只回复：找不到"
    )
    try:
        text = vision_analyze(path, prompt=prompt)
    except Exception:  # noqa: BLE001 - 视觉调用失败降级
        return None
    return _parse_coords(text)


def gui_locate_visual(description: str = "", window: str = "") -> str:
    """视觉接管定位：截图 + 视觉模型（qwen-vl，或配置的 UI-TARS）返回目标中心坐标。

    pywinauto 定位不到的自绘界面（游戏/定制控件/画布）时用视觉找目标；
    与 gui_locate（UIA 定位）互补，构成「定位不到 → 视觉接管」的降级链。

    Args:
        description: 要定位的元素描述（如按钮/输入框文本）
        window: 预留的窗口过滤参数（V1 全屏定位，窗口内裁剪留待增强）

    Returns:
        坐标或可读失败提示（静默降级，不抛错）。
    """
    if platform.system() != "Windows":
        return _non_windows_msg()
    if not description.strip():
        return "视觉定位失败：请提供要定位的元素描述（如按钮/输入框文本）。"
    coords = _visual_locate(description)
    if coords is None:
        return (
            f"视觉定位「{description}」失败：视觉模型不可用或图中未找到该元素。"
            "可先用 gui_screenshot 看屏确认，或提供目标坐标（x/y 参数）直接前台操作。"
        )
    return f"视觉定位「{description}」成功：中心坐标 ({coords[0]},{coords[1]})。"


# ------------------------------------------------------------------ #
# GUI 元素记忆库集成（N58，ADR-055）
#   定位成功自动存记忆；定位不到自动查记忆兜底 —— 越用越不用重新定位
# ------------------------------------------------------------------ #
def _mem_save(win, window_keyword: str, description: str, ctl) -> None:
    """定位成功后把「app + 描述 → 坐标/控件线索」写入元素记忆库（失败静默）。"""
    try:
        from handlers.gui_memory import gui_mem_save

        r = ctl.rectangle()
        app = (window_keyword or "").strip()
        if not app and win is not None:
            app = win.window_text() or ""
        gui_mem_save(
            app=app,
            description=description,
            window_class=win.class_name() if win is not None else "",
            control_type=ctl.friendly_class_name() or "",
            control_text=ctl.window_text() or "",
            x=r.left + r.width // 2,
            y=r.top + r.height // 2,
        )
    except Exception:  # noqa: BLE001 - 记忆写失败不阻断定位
        return


def _mem_hint(win, description: str) -> str:
    """定位不到时查记忆，返回「记忆命中」提示（无则空串）。"""
    try:
        from handlers.gui_memory import gui_mem_lookup

        app = win.window_text() if win is not None else ""
        rows = gui_mem_lookup(app=app, description=description, top=1)
        if not rows and app:
            rows = gui_mem_lookup(description=description, top=1)
        if rows:
            r = rows[0]
            return (
                f"\n记忆命中：曾在 [{r['app']}] 定位到「{r['description']}」"
                f"@ ({r['x']},{r['y']})（{r['control_type'] or '?'}，×{r['hits']}）。"
                "可用 gui_click 前台坐标直接操作，或 gui_locate_visual 复核。"
            )
    except Exception:  # noqa: BLE001 - 记忆读失败按无记忆处理
        pass
    return ""


def _mem_best_coords(win, description: str) -> tuple[int, int] | None:
    """查记忆里的目标中心坐标；无则 None。供 gui_click 未定位到时兜底。"""
    if not description.strip():
        return None
    try:
        from handlers.gui_memory import gui_mem_lookup

        app = win.window_text() if win is not None else ""
        rows = gui_mem_lookup(app=app, description=description, top=1)
        if not rows and app:
            rows = gui_mem_lookup(description=description, top=1)
        if rows and rows[0]["x"] and rows[0]["y"]:
            return int(rows[0]["x"]), int(rows[0]["y"])
    except Exception:  # noqa: BLE001 - 记忆读失败按无记忆处理
        return None
    return None


# ------------------------------------------------------------------ #
# 记忆兜底自愈闭环（N58 增强）：点击 → 复核 → 置信度 → 遗忘 + 重定位
# ------------------------------------------------------------------ #
def _img_dhash(path: str) -> int | None:
    """图片 dHash（差异哈希）→ 64bit 整数；PIL 缺失 / 读图失败返回 None。"""
    if not path:
        return None
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - 缺 PIL 降级
        return None
    try:
        img = Image.open(path).convert("L").resize((9, 8))
        px = list(img.getdata())
    except Exception:  # noqa: BLE001 - 读图失败降级
        return None
    h = 0
    for row in range(8):
        for col in range(8):
            h = (h << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return h


def _click_effect_changed(before_shot: str, after_shot: str, win, description: str) -> tuple[str, str]:
    """复核「记忆命中点击」是否生效（N58 自愈闭环核心）。

    信号分级（草案 L1→L2→unknown）：
    - L1（强）：点击后重新 UIA 定位同 description 控件，能找到说明界面确实变了
    - L2（辅助）：点击前后截图 dHash 汉明距离，> 阈值判变化，几乎一致判未变化
    - 都拿不到 → unknown（不判定，保留记忆）

    Returns:
        (status, evidence)：status ∈ {"changed", "unchanged", "unknown"}
    """
    # L1：点击后 UIA 是否已能定位（记忆兜底场景下点击前是定位不到的）
    if win is not None:
        try:
            if _find_control(win, description) is not None:
                return "changed", "点击后 UIA 已能定位到该控件"
        except Exception:  # noqa: BLE001 - 单个控件取属性失败跳过
            pass
    # L2：截图 dHash 差异
    b = _img_dhash(_extract_path(before_shot))
    a = _img_dhash(_extract_path(after_shot))
    if b is not None and a is not None:
        dist = bin(a ^ b).count("1")
        if dist > _DHASH_THRESHOLD:
            return "changed", "操作前后截图差异明显"
        return "unchanged", "操作前后截图几乎未变化"
    return "unknown", "复核信号不可用（缺 PIL 或截图失败）"


def _relocate_and_resave(win, window_keyword: str, description: str) -> str:
    """遗忘后自动视觉重定位并重新记入记忆库（N58 自愈闭环收尾）。"""
    coords = _visual_locate(description)
    if not coords:
        return "记忆已遗忘（旧坐标失效），但视觉重新定位也失败，需人工重新调教该元素。"
    x, y = coords
    app = (window_keyword or "").strip() or (win.window_text() if win is not None else "")
    try:
        from handlers.gui_memory import gui_mem_save

        gui_mem_save(app=app, description=description, x=x, y=y)
    except Exception:  # noqa: BLE001 - 记忆写失败不阻断重定位后的点击
        pass
    return _click_xy(x, y) + f"（记忆已遗忘旧坐标，视觉重定位「{description}」@ ({x},{y}) 并重新记入元素记忆库）"


def _click_with_memory_verify(x: int, y: int, win, window_keyword: str, description: str) -> str:
    """记忆兜底点击 + 自愈复核（N58 闭环入口）。

    截图(前) → 坐标点击 → 延迟 → 截图(后) → 判定生效与否 →
    gui_mem_verify 更新置信度 → 连续失效达阈值则遗忘 + 视觉重定位重记。
    """
    before = gui_screenshot()
    first_msg = _click_xy(x, y)
    time.sleep(_VERIFY_DELAY)
    after = gui_screenshot()
    status, evidence = _click_effect_changed(before, after, win, description)

    if status == "unknown":
        return f"{first_msg}（元素记忆命中「{description}」；本次无法确认点击效果，已保留记忆）"

    app = (window_keyword or "").strip() or (win.window_text() if win is not None else "")
    try:
        from handlers.gui_memory import gui_mem_verify

        vr = gui_mem_verify(app, description, status == "changed")
    except Exception:  # noqa: BLE001 - 复核写失败按无记忆处理
        vr = "missing"

    if status == "changed":
        return f"{first_msg}（元素记忆命中「{description}」，点击生效，记忆置信度已更新）"
    # unchanged：点击可能未生效
    if vr == "forgotten":
        return _relocate_and_resave(win, window_keyword, description)
    return (
        f"{first_msg}（元素记忆命中但点击可能未生效（{evidence}），"
        "已累积失效记录；连续失效达阈值将自动遗忘并重新定位）"
    )
