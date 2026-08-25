"""STT：语音转文字（faster-whisper）。

faster-whisper 比 openai-whisper 快 4x、省显存，本地 CPU 即可跑。
默认 tiny 中文，加载失败会提示模型下载与镜像设置。
"""

from __future__ import annotations

import os
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np

# 国内 Hugging Face 镜像（从环境变量可覆盖）
HF_ENDPOINT_DEFAULT = os.environ.get(
    "HF_ENDPOINT", "https://hf-mirror.com"
)
# faster-whisper 的 Hugging Face 命名空间
_FASTER_WHISPER_REPO = "Systran"


def _print_hf_hint(model_size: str, network_err: BaseException) -> None:
    """Hugging Face 模型下载失败时给出可执行的修复指令。"""
    import shutil

    cache_dir = Path(
        os.environ.get("HF_HUB_CACHE")
        or Path.home() / ".cache" / "huggingface" / "hub"
    )
    print("\n[STT] 无法自动下载 Whisper 模型。可能原因：")
    print(f"       1) 无法访问 Hugging Face（当前用 {HF_ENDPOINT_DEFAULT}）")
    print(f"       2) 本机无缓存，且未联网")
    print(f"\n解决方法（任选其一）：")
    print(f"  A. 用国内镜像：set HF_ENDPOINT=https://hf-mirror.com   （已默认设置）")
    print(f"  B. 手动下载后放到缓存目录：")
    print(f"     请从 https://hf-mirror.com/{_FASTER_WHISPER_REPO}/faster-whisper-{model_size}/tree/main")
    print(f"     下载所有文件到：{cache_dir}")
    print(f"     然后再运行一次即可。")
    print(f"  C. 降低模型大小（tiny=75MB / base=145MB / small=461MB）：")
    print(f"     set CHUAN_STT_MODEL=tiny  （默认 tiny）")
    if shutil.which("git"):
        print(f"  D. 用 Git LFS 拉：")
        print(f'     git clone https://hf-mirror.com/{_FASTER_WHISPER_REPO}/faster-whisper-{model_size}')
    print(f"\n原始错误：{network_err.__class__.__name__}: {network_err}")


MT5_FILES = ("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")


def find_cached_faster_whisper(model_size: str) -> str | None:
    """在 HF 缓存里找「完整」的 faster-whisper-<model_size> 模型目录。

    完整 = 同时包含 config.json / tokenizer.json / vocabulary.txt / model.bin。
    因为之前下载常因 401 只落小文件、缺核心权重 model.bin，导致缓存半残、
    又被 faster-whisper 判为不存在而反复联网。找到则返回快照绝对路径
    （faster-whisper 支持把路径当 model_size 传入，直接离线加载）。
    """
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache/huggingface/hub")
    repo_dir = hub / f"models--Systran--faster-whisper-{model_size}"
    snapshot_dir = repo_dir / "snapshots"
    if not snapshot_dir.is_dir():
        return None
    # 可能有多个快照（不同版本/branch），取第一个完整的
    for snap in sorted(snapshot_dir.iterdir()):
        if snap.is_dir() and all(
            (snap / name).exists() for name in MT5_FILES
        ):
            return str(snap)
    return None


class STTEngine:
    """语音转文字引擎，封装 faster-whisper / openai-whisper。"""

    def __init__(
        self,
        model_size: str | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
        local_path: str | None = None,
    ) -> None:
        self.model_size = model_size or os.environ.get(
            "CHUAN_STT_MODEL", "tiny"
        )
        # 显式本地路径优先；否则自动在 HF 缓存中找完整模型（含 model.bin）
        # 默认 tiny 缓存不完整时，自动回退到已完整下载的 base
        self._local_path = local_path or find_cached_faster_whisper(self.model_size)
        if self._local_path is None and self.model_size == "tiny":
            self._local_path = find_cached_faster_whisper("base")
        if local_path is not None:
            self._local_path = local_path
        # tiny 模型中文准确率有限，强制语言以提高召回
        self.language = language or os.environ.get("CHUAN_STT_LANGUAGE")
        if self.language is None and self.model_size in ("tiny", "base"):
            self.language = "zh"
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None
        self._backend: str = ""

    def load(self) -> None:
        """加载模型。优先本地缓存 → 再尝试下载 → 最后报错并提示。"""
        # 设置 HF 镜像（越早设越好）
        if "HF_ENDPOINT" not in os.environ:
            os.environ["HF_ENDPOINT"] = HF_ENDPOINT_DEFAULT

        # 1. 试 faster-whisper
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            # 回退 openai-whisper
            try:
                import whisper

                self._model = whisper.load_model(self.model_size)
                self._backend = "openai-whisper"
                return
            except ImportError as exc:
                raise ImportError(
                    "STT 需要安装 faster-whisper 或 openai-whisper。\n"
                    "pip install faster-whisper  # 推荐\n"
                    "pip install openai-whisper  # 回退"
                ) from exc

        # 2. faster-whisper：本地完整路径优先（离线，绕开 HF 联网校验/401）
        if self._local_path:
            try:
                self._model = WhisperModel(
                    self._local_path,
                    device=self._device,
                    compute_type=self._compute_type,
                    local_files_only=True,
                )
                self._backend = "faster-whisper+local"
                return
            except Exception:  # noqa: BLE001 - 本地路径不可用，继续走缓存/下载
                pass

        # 3. faster-whisper：按名字 local-only，命中官方缓存就不联网
        try:
            self._model = WhisperModel(
                self.model_size,
                device=self._device,
                compute_type=self._compute_type,
                local_files_only=True,
            )
            self._backend = "faster-whisper+local"
            return
        except Exception:  # noqa: BLE001 - 缓存未命中，继续走下载
            pass

        # 4. 尝试联网下载
        try:
            print(
                f"[STT] 首次加载 faster-whisper-{self.model_size}，正在从 {HF_ENDPOINT_DEFAULT} 下载…"
                f" （这可能需要几分钟，之后会缓存）"
            )
            self._model = WhisperModel(
                self.model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            self._backend = "faster-whisper"
        except Exception as exc:  # noqa: BLE001
            # 下载失败：给提示，不抛无意义大栈
            _print_hf_hint(self.model_size, exc)
            raise SystemExit(1)

    @property
    def backend(self) -> str:
        return self._backend

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """转写音频数组为文本。"""
        if self._model is None:
            self.load()

        if self._backend.startswith("faster-whisper"):
            segments, _info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                # 关闭上文条件生成：非语音/弱语音段不会再「复读」出重复文本
                condition_on_previous_text=False,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

        # openai-whisper 回退
        result = self._model.transcribe(audio, fp16=False, language=self.language)
        return result["text"].strip()

    def transcribe_file(self, wav_path: str | Path) -> str:
        """从 WAV 文件转写。"""
        path = Path(wav_path)
        with wave.open(str(path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return self.transcribe(audio)
