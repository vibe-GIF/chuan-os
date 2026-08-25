"""系统状态采集 handler —— 确定性采集本机 CPU/内存/磁盘/主机实时状态。

无 psutil，纯标准库实现：Linux 读 /proc/meminfo、Windows 用 ctypes
读 GlobalMemoryStatusEx、磁盘用 shutil.disk_usage、CPU 用 os.cpu_count()。
被 skills/system_status.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。

设计原则（N 本地资源感知）: 确定性、不依赖 LLM；任何采集项失败静默降级，
保证 agent 调用永远拿到可读文本，不抛错。
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path


def _fmt_bytes(n: float) -> str:
    """字节 → 人类可读（B/KB/MB/GB/TB）。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _cpu_info() -> str:
    """CPU：逻辑核心数 + POSIX 平均负载。"""
    cores = os.cpu_count() or 0
    text = f"逻辑核心: {cores}"
    if hasattr(os, "getloadavg"):
        try:
            load = os.getloadavg()
            text += f" · 负载(1/5/15min): {load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f}"
        except (OSError, AttributeError):
            pass
    return text


def _memory_info() -> str:
    """内存占用：Linux 读 /proc/meminfo；Windows 用 ctypes GlobalMemoryStatusEx。"""
    if os.name == "posix" and os.path.exists("/proc/meminfo"):
        try:
            info: dict = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:") or line.startswith("MemAvailable:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            info[parts[0].rstrip(":")] = int(parts[1]) * 1024
            total = info.get("MemTotal")
            avail = info.get("MemAvailable")
            if total and avail:
                used = total - avail
                pct = used / total * 100
                return (f"内存: 已用 {_fmt_bytes(used)} / 共 {_fmt_bytes(total)} "
                        f"({pct:.0f}%) · 可用 {_fmt_bytes(avail)}")
        except (OSError, ValueError, KeyError):
            pass
    elif os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total = stat.ullTotalPhys
                used = total - stat.ullAvailPhys
                pct = used / total * 100 if total else 0
                return (f"内存: 已用 {_fmt_bytes(used)} / 共 {_fmt_bytes(total)} "
                        f"({pct:.0f}%) · 可用 {_fmt_bytes(stat.ullAvailPhys)}")
        except Exception:
            pass
    return "内存: 当前平台暂无法从标准库读取"


def _disk_info() -> list:
    """各磁盘分区占用：Windows 枚举 A–Z:，POSIX 取根目录。"""
    roots: list = []
    if os.name == "nt":
        import string

        roots = [f"{l}:\\" for l in string.ascii_uppercase if Path(f"{l}:\\").exists()]
    else:
        roots = ["/"]
    lines: list = []
    for root in roots:
        try:
            usage = shutil.disk_usage(root)
            pct = usage.used / usage.total * 100 if usage.total else 0
            lines.append(
                f"{root} 已用 {_fmt_bytes(usage.used)} / 共 {_fmt_bytes(usage.total)} "
                f"({pct:.0f}%) · 剩余 {_fmt_bytes(usage.free)}"
            )
        except (OSError, ValueError):
            continue
    return lines


def system_status(paths: str = "") -> str:
    """采集本机系统实时状态（CPU/内存/磁盘/主机）。

    Args:
        paths: 额外要查看的磁盘路径（分号或逗号分隔）。

    Returns:
        状态文本；采集失败项自动降级，始终返回可读结果。
    """
    parts = [
        f"主机: {platform.system()} {platform.release()} ({platform.machine()})",
        f"CPU: {_cpu_info()}",
        _memory_info(),
    ]
    disk = _disk_info()
    parts.append("磁盘: " + (" | ".join(disk) if disk else "无法读取"))

    extra: list = []
    if paths:
        for chunk in str(paths).replace("，", ",").replace("；", ";").split(";"):
            for p in chunk.split(","):
                p = p.strip()
                if not p:
                    continue
                try:
                    u = shutil.disk_usage(p)
                    pct = u.used / u.total * 100 if u.total else 0
                    extra.append(
                        f"{p} 已用 {_fmt_bytes(u.used)} / 共 {_fmt_bytes(u.total)} ({pct:.0f}%)"
                    )
                except (OSError, ValueError):
                    extra.append(f"{p} 无法读取")
    if extra:
        parts.append("额外路径: " + " | ".join(extra))

    return "\n".join(parts)