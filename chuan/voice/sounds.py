"""事件音效：程序化合成（零素材文件）。

借鉴 assistant-x-openclaw 的 data/voices/ 事件音效体系（wake/thinking/
success/error/exit…），但不依赖 wav 素材——用正弦波+包络现场合成，
任何机器零依赖可用。

设计（对标 openclaw feedback.py）：
- play(event) 后台线程播放，不阻塞主流程
- 同一音效 0.5s 防抖（它的实测教训：快速连续事件会音效轰炸）
- 音量固定 0.4（短促提示音，不盖过 TTS 播报）
- sounddevice/numpy 缺失或 CHUAN_SOUNDS=0 时静默降级
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

import numpy as np

SAMPLE_RATE = 24000
DEBOUNCE_S = 0.5  # 同一音效 0.5s 内不重复（openclaw 实测经验）
DEFAULT_VOLUME = 0.4

# 事件 → 合成参数（每个音符：(频率Hz, 时长s)）
# 音高设计：上行=积极（成功/唤醒），下行=结束（退出），低频短促=错误
_EVENTS: dict[str, list[tuple[int, float]]] = {
    "init": [(523, 0.09), (659, 0.09), (784, 0.14)],   # C5-E5-G5 上行三连音
    "listen": [(880, 0.07)],                            # 单音轻响：开麦了
    "bargein": [(988, 0.06), (988, 0.06)],              # 双击高音：被打断转录音
    "thinking": [(440, 0.08), (494, 0.08)],             # A4-B4 轻升：正在思考
    "success": [(659, 0.09), (784, 0.09), (988, 0.16)], # E5-G5-B5 大三度琶音
    "error": [(196, 0.22)],                             # 低音短鸣：出错了
    "exit": [(784, 0.12), (523, 0.18)],                 # G5→C5 下行：再见
}


def _synth_notes(notes: list[tuple[int, float]], volume: float) -> np.ndarray:
    """把 (频率, 时长) 列表合成为带 ADSR 包络的连续波形。"""
    parts: list[np.ndarray] = []
    for freq, dur in notes:
        n = int(SAMPLE_RATE * dur)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        wave = np.sin(2 * np.pi * freq * t, dtype=np.float32)
        # 指数衰减包络：起音即峰值，自然衰减（更像电子提示音，无咔哒声）
        env = np.exp(-t / (dur * 0.35), dtype=np.float32)
        parts.append(wave * env)
    audio = np.concatenate(parts)
    return (audio * volume).astype(np.float32)


class SoundEngine:
    """事件音效引擎。用法：sound.play("success")。"""

    def __init__(self, volume: float = DEFAULT_VOLUME) -> None:
        self.volume = volume
        self.enabled = os.environ.get("CHUAN_SOUNDS", "1") != "0"
        self._last_time: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sd = None
        if self.enabled:
            try:
                import sounddevice as sd

                self._sd = sd
            except Exception:  # noqa: BLE001 - 无音频设备也允许跑
                self.enabled = False

    def play(self, event: str) -> None:
        """后台线程播放事件音效（防抖 + 静默失败）。"""
        if not self.enabled or event not in _EVENTS:
            return
        with self._lock:
            now = time.time()
            if now - self._last_time.get(event, 0.0) < DEBOUNCE_S:
                return
            self._last_time[event] = now
        threading.Thread(
            target=self._play_sync, args=(_EVENTS[event],), daemon=True
        ).start()

    def _play_sync(self, notes: list[tuple[int, float]]) -> None:
        try:
            assert self._sd is not None
            audio = _synth_notes(notes, self.volume)
            # 短音效直接 wait：线程后台跑，不阻塞调用方
            self._sd.play(audio, SAMPLE_RATE)
            self._sd.wait()
        except Exception:  # noqa: BLE001 - 音效失败绝不影响主流程
            pass

    # 便捷方法（对标 openclaw 的 system_ready/on_error/on_exit）
    def system_ready(self) -> None:
        self.play("init")

    def on_error(self) -> None:
        self.play("error")

    def on_exit(self) -> None:
        self.play("exit")


def role_voice_config(config_path) -> dict:
    """读取 config/voices.yaml 的角色→音色映射（延迟导入 yaml）。"""
    import yaml

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "default": data.get("default", "zh-CN-XiaoxiaoNeural"),
        "roles": data.get("roles", {}),
    }


def parse_role_prefix(reply: str) -> tuple[str | None, str]:
    """从「[角色] 内容」回复中拆出角色与正文。

    Returns:
        (角色名或 None, 正文)
    """
    if reply.startswith("["):
        end = reply.find("]")
        if 0 < end <= 12:  # 角色名很短，超出大概率是 markdown 链接之类
            return reply[1:end], reply[end + 1 :].strip()
    return None, reply


def _noop() -> None:
    """静默引擎占位（测试用）。"""


def make_sound_or_noop() -> SoundEngine | None:
    """工厂：音效可用返回引擎，否则 None（调用方判空即可，无需 try）。"""
    engine = SoundEngine()
    return engine if engine.enabled else None


__all__ = [
    "SoundEngine",
    "make_sound_or_noop",
    "parse_role_prefix",
    "role_voice_config",
    "_synth_notes",
    "_EVENTS",
    "_noop",
    "Callable",
]
