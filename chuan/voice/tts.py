"""TTS：文字转语音（edge-tts）。

edge-tts 使用微软 Edge 在线 TTS 服务，免费、高质量、低延迟。
不可用时回退 Windows SAPI / macOS say / Linux espeak。

金属感音色（移植自 assistant-x-openclaw 的 Jarvis）：合成后把音频过一遍
ffmpeg 滤镜链（回声/合唱/低音增强），制造钢铁侠 JARVIS 的机械质感。
ffmpeg 不可用时自动回退原声，不阻断播报。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

# 默认中文音色（晓晓），可按角色配置
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# 贾维斯金属感滤镜链（来自 assistant-x-openclaw assistants.json tts_config.metallic.af）
_METALLIC_AF = (
    "aecho=0.8:0.85:20|45|70:0.45|0.32|0.12,"
    "chorus=0.4:0.6:45:0.2:0.18:1.2,"
    "bass=g=4:f=110,treble=g=2.5,highpass=f=90,lowpass=f=8500"
)

_FFMPEG_BIN: str | None | bool = False  # False = 未探测；None = 无


def _ffmpeg_bin() -> str | None:
    """解析 ffmpeg 路径：FFMPEG_BIN 环境变量 → imageio-ffmpeg 自带 → 系统 PATH。"""
    global _FFMPEG_BIN
    if _FFMPEG_BIN is not False:
        return _FFMPEG_BIN or None
    cand = os.environ.get("FFMPEG_BIN")
    if not cand:
        try:
            import imageio_ffmpeg  # type: ignore[import-not-found]

            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.isfile(exe):
                cand = exe
        except Exception:  # noqa: BLE001 - 未安装走系统兜底
            pass
    if not cand:
        cand = shutil.which("ffmpeg")
    _FFMPEG_BIN = cand or None
    return cand


def apply_metallic(data: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """把音频过 ffmpeg 金属链。失败/无 ffmpeg 时原样返回，不阻断播报。"""
    ff = _ffmpeg_bin()
    if not ff:
        return data, sr
    try:
        import io

        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, data, sr, format="WAV", subtype="FLOAT")
        proc = subprocess.run(
            [
                ff, "-hide_banner", "-loglevel", "error",
                "-f", "wav", "-i", "pipe:0",
                "-af", _METALLIC_AF,
                "-f", "wav", "pipe:1",
            ],
            input=buf.getvalue(),
            capture_output=True,
        )
        if proc.returncode != 0 or not proc.stdout:
            return data, sr
        out, osr = sf.read(io.BytesIO(proc.stdout), dtype="float32")
        if out.ndim > 1:
            out = out.mean(axis=1)
        return out.astype(np.float32), int(osr)
    except Exception:  # noqa: BLE001 - 金属处理失败回退原声
        return data, sr


class TTSEngine:
    """文字转语音引擎，封装 edge-tts + 系统回退。

    支持后台播报与打断：
    - speak_async() 在后台线程播报，不阻塞主流程
    - stop() 立即停止当前播报（打断）
    """

    def __init__(self, voice: str = DEFAULT_VOICE) -> None:
        self.voice = voice
        self._edge_available: bool | None = None
        # 播报代际：stop() 自增。旧代际的合成结果完成后直接丢弃，不再播放
        self._generation = 0

    def speak(
        self,
        text: str,
        voice: str | None = None,
        rate: str | None = None,
        metallic: bool = False,
    ) -> None:
        """同步播报文本（阻塞直到播放完或被打断）。

        voice: 本次播报用的音色（None 用默认）。按次传入而非改实例
            属性，避免后台线程竞态。
        rate: 语速（edge-tts 格式，如 "+15%"）。
        metallic: 是否过金属滤镜链（贾维斯音色）。
        """
        text = text.strip()
        if not text:
            return

        gen = self._generation
        if self._try_edge_tts(text, gen, voice=voice, rate=rate, metallic=metallic):
            return
        if self._generation != gen:
            return  # 合成期间被打断，跳过
        self._system_tts(text)

    def speak_async(
        self,
        text: str,
        voice: str | None = None,
        rate: str | None = None,
        metallic: bool = False,
    ) -> threading.Thread | None:
        """后台线程播报，返回线程对象；空文本返回 None。"""
        text = text.strip()
        if not text:
            return None
        t = threading.Thread(
            target=self.speak,
            args=(text,),
            kwargs={"voice": voice, "rate": rate, "metallic": metallic},
            daemon=True,
        )
        t.start()
        return t

    def stop(self) -> None:
        """立即停止当前播报（打断）。

        播放中：sounddevice 立即静音；
        合成中：代际+1，合成完成后不再播放。
        """
        self._generation += 1
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001 - 未安装/无播放时静默
            pass

    def speak_to_file(self, text: str, output_path: str | Path) -> bool:
        """用 edge-tts 合成音频到文件。成功返回 True。"""
        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError:
            return False

        async def _synth() -> None:
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(str(output_path))

        asyncio.run(_synth())
        return True

    def _try_edge_tts(
        self,
        text: str,
        gen: int,
        voice: str | None = None,
        rate: str | None = None,
        metallic: bool = False,
    ) -> bool:
        """用 edge-tts 合成并播放。"""
        if self._edge_available is False:
            return False

        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError:
            self._edge_available = False
            return False

        tmp = tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False, dir=tempfile.gettempdir()
        )
        tmp_path = tmp.name
        tmp.close()

        try:
            asyncio.run(self._synth_edge(edge_tts, text, tmp_path, voice, rate))
            if self._generation != gen:
                return True  # 合成期间被打断，丢弃
            self._play_audio(tmp_path, metallic=metallic)
            self._edge_available = True
            return True
        except Exception:  # noqa: BLE001
            self._edge_available = False
            return False
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def _synth_edge(
        self,
        edge_tts: Any,
        text: str,
        path: str,
        voice: str | None = None,
        rate: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if rate:
            kwargs["rate"] = rate
        communicate = edge_tts.Communicate(text, voice or self.voice, **kwargs)
        await communicate.save(path)

    def _play_audio(self, path: str, metallic: bool = False) -> None:
        """播放音频文件。优先库内解码播放（soundfile+sounddevice），
        不弹外部播放器；库不可用才回退系统关联程序。"""
        # 1) 库内播放：解码 mp3 后直接送声卡，绝不弹 PotPlayer 等外部程序
        try:
            import sounddevice as sd
            import soundfile as sf

            data, sr = sf.read(path, dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            if metallic:
                data, sr = apply_metallic(np.asarray(data, dtype=np.float32), int(sr))
            sd.play(data, sr)
            sd.wait()
            return
        except Exception:  # noqa: BLE001 - 缺库/解码失败，走系统回退
            pass

        # 2) 回退：系统关联程序（可能弹外部播放器）
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["afplay", path], check=False, capture_output=True)
        else:
            for player in ("mpv", "ffplay", "aplay"):
                try:
                    subprocess.run(
                        [player, path], check=False, capture_output=True
                    )
                    return
                except FileNotFoundError:
                    continue

    def _system_tts(self, text: str) -> None:
        """系统 TTS 回退。"""
        if sys.platform == "darwin":
            subprocess.run(["say", text], capture_output=True)
        elif sys.platform == "linux":
            try:
                subprocess.run(["espeak", text], capture_output=True)
            except FileNotFoundError:
                pass
        elif sys.platform == "win32":
            # Windows SAPI
            try:
                subprocess.run(
                    [
                        "powershell",
                        "-Command",
                        f'Add-Type -AssemblyName System.Speech; '
                        f'(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("{text}")',
                    ],
                    capture_output=True,
                )
            except Exception:  # noqa: BLE001
                pass
