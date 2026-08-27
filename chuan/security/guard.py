"""陌生人识别 + 自动锁屏（P4 安全增强 / N59，ADR-057）。

基于 N54 声纹库（``chuan/voice/spoof.py``）做「识别说话人」：
- ``identify_speaker(audio)``：遍历已注册声纹取最高相似度分，低于阈值判
  「陌生人」（None）；无注册声纹 / 音频不可靠（过短/静音/低能量）→ unknown，
  不轻易判陌生人（避免环境声误锁屏）。
- ``lock_workstation()``：Windows 锁屏（ctypes ``user32.LockWorkStation``），
  非 Windows / 失败静默降级返回 False。
- ``SecurityGuard``：配置驱动（config ``security.lock``，默认关）。防抖：连续
  ``streak`` 次陌生人判定才锁屏（防单次误判），全程旁路 try/except 不阻断语音
  主循环。

用法（voice 主循环阶段 3）：
    verdict = guard.check(utterance.audio)   # 返回判定 dict
    if verdict.get("locked"):
        print("⚠ 检测到陌生人声纹，已自动锁屏")
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from chuan.voice import spoof

# 音频不可靠判定复用 spoof 的一级反欺骗阈值（低于此不判陌生人）
_MIN_DURATION = spoof.MIN_DURATION
_SILENCE_RATIO_MAX = spoof.SILENCE_RATIO_MAX
_MIN_ENERGY = spoof.MIN_ENERGY

_DEFAULT_THRESHOLD = 0.6  # 相似度低于此判陌生人
_DEFAULT_STREAK = 3  # 连续 N 次陌生人判定才锁屏


def _config_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"


def _load_lock_config() -> dict[str, Any]:
    """读 config.yaml security.lock 段；读不到给默认（全关）。"""
    default = {
        "enabled": False,
        "threshold": _DEFAULT_THRESHOLD,
        "streak": _DEFAULT_STREAK,
    }
    try:
        import yaml

        with _config_path().open("r", encoding="utf-8") as f:
            sec = (yaml.safe_load(f) or {}).get("security") or {}
        lock = sec.get("lock") or {}
        if not lock.get("enabled"):
            return default
        return {
            "enabled": True,
            "threshold": float(lock.get("threshold", _DEFAULT_THRESHOLD)),
            "streak": max(1, int(lock.get("streak", _DEFAULT_STREAK))),
        }
    except Exception:  # noqa: BLE001 - 配置缺失/损坏 → 全关
        return default


def identify_speaker(
    audio: np.ndarray,
    threshold: float = _DEFAULT_THRESHOLD,
    sample_rate: int = spoof.SAMPLE_RATE,
) -> tuple[str | None, float]:
    """识别说话人 → ``(名字 | None=陌生人 | "unknown", 最高相似度分)``。

    规则：
    - 音频不可靠（过短/静音占比过高/能量过低）→ (None, 0.0)（unknown，不判陌生人）
    - 无任何注册声纹 → (None, 0.0)（unknown，旁路）
    - 遍历已注册声纹取最高相似度分；>= threshold → (name, score)（owner）
    - 最高分 < threshold → (None, score)（stranger）
    """
    try:
        fx = spoof.extract_features(audio, sample_rate)
        # 一级反欺骗：音频不可靠时不判陌生人（避免环境声/回放碎片误锁屏）
        if (
            fx["duration_s"] < _MIN_DURATION
            or fx["silence_ratio"] >= _SILENCE_RATIO_MAX
            or fx["rms_mean"] < _MIN_ENERGY
        ):
            return None, 0.0  # unknown

        best_name: str | None = None
        best_score = -1.0
        for name in spoof.list_speakers():
            enrolled = spoof.load_speaker(name)
            if enrolled is None:
                continue
            score = spoof._compare_voiceprint(enrolled, fx)  # noqa: SLF001 - 复用 N54 比对
            if score > best_score:
                best_score = score
                best_name = name

        if best_name is None:
            return None, 0.0  # unknown：无注册声纹
        if best_score >= threshold:
            return best_name, best_score
        return None, best_score  # stranger
    except Exception:  # noqa: BLE001 - 识别失败按 unknown 旁路
        return None, 0.0


def lock_workstation() -> bool:
    """锁定 Windows 工作站（Win+L 等价）。非 Windows / 失败返回 False。

    注意：锁屏后当前进程不终止，语音循环可继续；解锁靠系统凭据。
    """
    if os.name != "nt":
        return False
    try:
        ok = ctypes.windll.user32.LockWorkStation()
        return bool(ok)
    except Exception:  # noqa: BLE001 - 无桌面会话（服务态）等静默降级
        return False


@dataclass
class SecurityGuard:
    """配置驱动的安全哨兵：陌生人识别 + 连续判定防抖锁屏。

    属性可注入（测试用）；``check()`` 不抛错，任何失败返回 disabled/unknown。
    """

    enabled: bool = False
    threshold: float = _DEFAULT_THRESHOLD
    streak: int = _DEFAULT_STREAK
    sample_rate: int = spoof.SAMPLE_RATE
    lock_cb: Any = None  # 可注入的锁屏回调（默认 lock_workstation）

    _fail_streak: int = 0

    def __post_init__(self) -> None:
        if self.lock_cb is None:
            self.lock_cb = lock_workstation

    @classmethod
    def from_config(cls) -> "SecurityGuard":
        """从 config.yaml security.lock 构造（默认关）。"""
        cfg = _load_lock_config()
        return cls(
            enabled=cfg["enabled"],
            threshold=cfg["threshold"],
            streak=cfg["streak"],
        )

    def reset(self) -> None:
        """重置防抖计数（如用户主动打断/手动解锁后）。"""
        self._fail_streak = 0

    def check(self, audio: np.ndarray) -> dict[str, Any]:
        """判定一句音频。返回 dict（enabled/name/score/verdict/streak/locked/reason）。

        verdict: disabled / unknown / owner / stranger
        locked: 仅连续 streak 次 stranger 且 enabled 时 True
        """
        if not self.enabled:
            return {
                "enabled": False,
                "name": None,
                "score": 0.0,
                "verdict": "disabled",
                "streak": self._fail_streak,
                "locked": False,
                "reason": "安全哨兵未启用",
            }
        try:
            name, score = identify_speaker(audio, self.threshold, self.sample_rate)
            if name is None and score <= 0.0:
                return {
                    "enabled": True,
                    "name": None,
                    "score": score,
                    "verdict": "unknown",
                    "streak": self._fail_streak,
                    "locked": False,
                    "reason": "音频不可靠或无注册声纹，跳过判定",
                }
            if name is not None:  # owner：清零防抖
                self._fail_streak = 0
                return {
                    "enabled": True,
                    "name": name,
                    "score": score,
                    "verdict": "owner",
                    "streak": 0,
                    "locked": False,
                    "reason": f"声纹匹配「{name}」（{score:.2f}）",
                }
            # stranger：连续 streak 次才锁屏
            self._fail_streak += 1
            locked = self._fail_streak >= self.streak
            if locked:
                self._fail_streak = 0  # 锁屏后复位，重新计数
                try:
                    if self.lock_cb:
                        self.lock_cb()
                except Exception:  # noqa: BLE001 - 锁屏失败不阻断
                    pass
            return {
                "enabled": True,
                "name": None,
                "score": score,
                "verdict": "stranger",
                "streak": self._fail_streak,
                "locked": locked,
                "reason": (
                    f"陌生人声纹（{score:.2f} < {self.threshold}）"
                    + ("，已自动锁屏" if locked else "")
                ),
            }
        except Exception:  # noqa: BLE001 - 判定失败按 unknown 旁路
            return {
                "enabled": True,
                "name": None,
                "score": 0.0,
                "verdict": "unknown",
                "streak": self._fail_streak,
                "locked": False,
                "reason": "判定异常，跳过",
            }
