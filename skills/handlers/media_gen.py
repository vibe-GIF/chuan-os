"""媒体生成 handler —— 音乐程序化合成 + 视频/图片后端占位（P4，V1）。

被 skills/media_gen.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。

设计（N56 媒体生成 V1）:
- **music**：纯 numpy 程序化合成（正弦 + 指数包络 + 和弦琶音），用标准库 wave
  写 16-bit PCM wav——零新增依赖，任何机器可跑（对齐 voice/sounds.py 的合成惯例）；
  prompt 关键词影响调式/速度（欢快=大调+快、悲伤=小调+慢），确定性可测；
- **video / image**：后端占位（项目侧已装 seedance/seedream 插件能力，但运行时
  无直连 API）→ 返回可读提示，绝不抛错；
- 任何失败静默降级返回可读文本（对齐项目惯例）。
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import numpy as np

# 项目根（skills/handlers/media_gen.py → skills/handlers → skills → 根）
_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_RATE = 44100
_AMP = 0.28
_TITLE_MAX = 30  # 文件名里 prompt 截断长度

# 和弦进行（频率 Hz）：C 大调 C-G-Am-F；A 小调 Am-F-C-G
_MAJOR_CHORDS = [
    [261.63, 329.63, 392.00],  # C
    [196.00, 246.94, 293.66],  # G
    [220.00, 261.63, 329.63],  # Am
    [174.61, 220.00, 261.63],  # F
]
_MINOR_CHORDS = [
    [220.00, 261.63, 329.63],  # Am
    [174.61, 220.00, 261.63],  # F
    [261.63, 329.63, 392.00],  # C
    [196.00, 246.94, 293.66],  # G
]

_FAST_WORDS = ("欢快", "轻快", "快", "明亮", "upbeat", "fast", "happy")
_SLOW_WORDS = ("悲伤", "低沉", "伤感", "慢", "抒情", "sad", "slow", "dark")


def _tone(freq: float, dur: float, sr: int = SAMPLE_RATE, amp: float = _AMP) -> np.ndarray:
    """单个音符：正弦 + 指数衰减包络（对齐 sounds.py 防咔哒惯例）。"""
    n = max(1, int(sr * dur))
    t = np.arange(n, dtype=np.float32) / sr
    wave = np.sin(2 * np.pi * float(freq) * t, dtype=np.float32)
    env = np.exp(-t / max(dur * 0.4, 1e-3), dtype=np.float32)
    return (wave * env * amp).astype(np.float32)


def _synth_music(prompt: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """按 prompt 情绪合成一段和弦琶音（确定性）。"""
    text = (prompt or "").lower()
    minor = any(w in text for w in _SLOW_WORDS)
    if any(w in text for w in _FAST_WORDS):
        bpm = 150
    elif minor:
        bpm = 70
    else:
        bpm = 110
    chords = _MINOR_CHORDS if minor else _MAJOR_CHORDS
    note_dur = (60.0 / bpm / 2) * 0.9  # 16 分音符（留呼吸）
    parts: list[np.ndarray] = []
    for chord in chords:
        arp = [chord[0], chord[1], chord[2], chord[0] * 2.0]  # 根-三-五-高八度根
        for freq in arp:
            parts.append(_tone(freq, note_dur, sr=sr))
    return np.concatenate(parts) if parts else np.zeros(1, np.float32)


def _write_wav(path: Path, audio: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """float32(-1..1) → 16-bit PCM wav（标准库 wave）。"""
    import wave

    pcm = np.clip(audio, -1.0, 1.0)
    data = (pcm * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def _out_dir(output_dir: str) -> Path:
    d = Path(output_dir) if output_dir else _ROOT / "data" / "media"
    if not d.is_absolute():
        d = _ROOT / d
    return d


def media_generate(kind: str = "music", prompt: str = "", output_dir: str = "") -> str:
    """媒体生成：音乐合成（写 wav）/ 视频 / 图片（后端占位）。

    Args:
        kind: music / video / image。
        prompt: 描述（音乐时影响情绪/速度）。
        output_dir: 输出目录（缺省 data/media，相对项目根或绝对路径）。

    Returns:
        成功返回文件路径文本；未实现/失败返回可读提示（静默降级，不抛错）。
    """
    kind = (kind or "").strip().lower()
    try:
        d = _out_dir(output_dir)
        d.mkdir(parents=True, exist_ok=True)

        if kind == "music":
            audio = _synth_music(prompt)
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = re.sub(r"[^\w\u4e00-\u9fff]+", "", prompt or "bgm")[:_TITLE_MAX]
            path = d / f"music_{name or 'bgm'}_{stamp}.wav"
            _write_wav(path, audio)
            if not path.is_file() or path.stat().st_size <= 44:  # wav 头以上才算有效
                return "（媒体生成：合成写入失败）"
            return f"已生成音乐 → {path}（{path.stat().st_size} 字节，采样率 {SAMPLE_RATE}Hz）"

        if kind == "video":
            return "（媒体生成：视频后端未接入——待接 seedance API，配置密钥后可用）"
        if kind == "image":
            return "（媒体生成：图片后端未接入——待接 seedream API，配置密钥后可用）"

        return f"（媒体生成：未知类型「{kind}」，支持 music / video / image）"
    except Exception as exc:  # noqa: BLE001 - 任何失败静默降级
        return f"（媒体生成失败：{exc}）"
