"""桌面状态采集 handler —— 确定性采集当前活动窗口/屏幕分辨率。
Windows 用 ctypes WinAPI（GetForegroundWindow / GetSystemMetrics），其他平台静默降级。
被 skills/desktop_status.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。
"""

from __future__ import annotations

import ctypes
import platform


def _win_user32():
    """惰性加载 user32，避免非 Windows 平台 import ctypes.WinDLL 报错。"""
    return ctypes.WinDLL("user32", use_last_error=True)


def _active_window_title() -> str:
    """Windows 当前前台窗口标题；无窗口或失败返回 'N/A'。"""
    try:
        user32 = _win_user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "N/A"
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or "N/A"
    except Exception:
        return "N/A"


def _screen_resolution() -> str:
    """Windows SM_CXSCREEN/SM_CYSCREEN 主屏分辨率；失败返回 'N/A'。"""
    try:
        user32 = _win_user32()
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return f"{w}x{h}"
        return "N/A"
    except Exception:
        return "N/A"


def desktop_status() -> str:
    """采集桌面环境状态：屏幕分辨率 + 当前活动窗口标题。"""
    if platform.system() != "Windows":
        return "桌面监控目前支持 Windows；当前平台无法采集。"
    return "\n".join(
        [
            f"屏幕分辨率: {_screen_resolution()}",
            f"当前活动窗口: {_active_window_title()}",
        ]
    )