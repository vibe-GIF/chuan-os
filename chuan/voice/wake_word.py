"""唤醒词检测（OpenWakeWord）。

OpenWakeWord 是本地离线唤醒词引擎，基于 ONNX 模型。
支持自定义唤醒词（如「小川小川」需训练或用 mwn 模型生成）。

不可用时回退「按 Enter 说话」模式（类似 jarvis）。
"""

from __future__ import annotations

from typing import Any

import numpy as np


class WakeWordDetector:
    """唤醒词检测器，封装 OpenWakeWord + 回退模式。"""

    def __init__(self, keyword: str = "chuan") -> None:
        self.keyword = keyword
        self.prompt: str = keyword  # 实际生效的唤醒短语，load 后可能回退为英文词
        self._model: Any = None
        self._available = False

    def load(self) -> bool:
        """加载唤醒词模型。成功返回 True。

        openwakeword 依赖 tflite-runtime，但 python>=3.13 无对应安装包；
        这里显式用 ONNX 推理框架（onnxruntime）加载 .onnx 模型。
        自定义词（keyword）需先放在 ~/.openwakeword/models/ 下；否则回退内置 hey_jarvis.onnx。
        """
        try:
            import os
            from openwakeword.model import Model

            models_dir = os.path.join(os.path.expanduser("~"), ".openwakeword", "models")
            # 优先 keyword 对应的 onnx 模型，回退内置 hey_jarvis
            candidates = []
            for name in (self.keyword.replace(" ", "_"), "hey_jarvis"):
                path = os.path.join(models_dir, f"{name}.onnx")
                if os.path.exists(path):
                    candidates.append(path)

            if not candidates:
                raise FileNotFoundError(
                    "未找到唤醒词 ONNX 模型，请将模型放入 ~/.openwakeword/models/"
                )

            self._model = Model(
                wakeword_models=[candidates[0]],
                inference_framework="onnx",
            )
            self._model_name = os.path.basename(candidates[0])
            # 生效的唤醒短语：如 hey_jarvis.onnx → "hey jarvis"。
            # 与配置的 keyword 是否一致，取决于加载到的是自定义词还是回退词。
            self.prompt = self._model_name[: -len(".onnx")].replace("_", " ")
            self._available = True
        except Exception:  # noqa: BLE001 - 任何加载失败（缺依赖/模型损坏/onnx报错）都回退不可用
            self._available = False
        return self._available

    @property
    def available(self) -> bool:
        return self._available

    def detect(self, audio_chunk: np.ndarray, sample_rate: int = 16000) -> bool:
        """检测音频块中是否包含唤醒词。

        openwakeword 期望 int16 尺度（-32768..32767）的音频；实时麦克风流是
        float32(-1..1)，这里统一放大到 int16 尺度再推理，否则模型恒输出 0。
        若调用方已传 int16 尺度，则不做二次放大。
        """
        if not self._available or self._model is None:
            return False
        try:
            peak = float(np.abs(audio_chunk).max()) if audio_chunk.size else 0.0
            if peak <= 1.0:  # 说明是 -1..1 归一化 float，需放大到 int16 尺度
                audio_chunk = audio_chunk * 32768.0
            prediction = self._model.predict(audio_chunk)
            # 返回 {model_name: score}，score > 0.5 视为命中
            for _name, score in prediction.items():
                if score > 0.5:
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False


def record_until_wake(
    detector: WakeWordDetector | None = None,
    sample_rate: int = 16000,
    chunk_duration: float = 0.16,
) -> np.ndarray | None:
    """持续录音直到检测到唤醒词（独立阻塞版，供无常开流的场景）。

    openwakeword 的 predict() 是有状态流式推理：内部自持滚动缓冲，必须按
    顺序、不重叠地喂块，重复喂重叠音频会污染内部状态导致恒低分。
    无 detector 时回退 None（调用方应提示按 Enter）。
    """
    if detector is None or not detector.available:
        return None

    import sounddevice as sd

    chunk_samples = int(sample_rate * chunk_duration)
    collected: list[np.ndarray] = []
    while True:
        chunk = sd.rec(
            chunk_samples,
            samplerate=sample_rate,
            channels=1,
            dtype=np.float32,
        )
        sd.wait()
        audio = chunk.flatten()
        collected.append(audio)
        if detector.detect(audio, sample_rate):
            return np.concatenate(collected)
