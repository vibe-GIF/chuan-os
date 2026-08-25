"""语音交互主循环。

架构（v2，借鉴 assistant-x-openclaw 的常开流设计）：
- 麦克风流常开（回调入队），永不 stop/close——规避 PortAudio 反复开关流
  在部分驱动上的死锁/丢流（Realtek 麦克风阵列实测丢流）
- 说话开始：按 Enter（防止后台视频人声误触发对话）
- 播报打断（barge-in）：TTS 播报期间麦克风持续监听，检测到高于回声门限的
  真人声即停止播报并转入录音——开口就能打断，无需按键
- 人声判定：Silero VAD（随 faster-whisper 自带，需 onnxruntime），
  纯音乐/键盘声不触发；VAD 不可用时退化为能量阈值

唤醒词模式（openwakeword 可用时）：保持旧的「唤醒词 → Enter → 阻塞录音」流程。

核心设计：语音壳稳定、主脑可插拔（复用 RuntimeSupervisor 的 agent/工具/记忆）。
"""

from __future__ import annotations

import os
import queue
import random
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from chuan.runtime_supervisor import RuntimeSupervisor
from chuan.channels.hud import HudChannel, push_monitor_snapshot
from chuan.voice.sounds import SoundEngine, parse_role_prefix, role_voice_config
from chuan.voice.stt import STTEngine
from chuan.voice.tts import TTSEngine
from chuan.voice.wake_word import WakeWordDetector

SAMPLE_RATE = 16000
CHUNK_DUR = 0.16
# 2560 = 5 × 512（Silero VAD 要求输入长度是 512 的整数倍）
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DUR)

_EXIT_WORDS = {"exit", "quit", "退出", "再见", "拜拜", "关闭"}
_STRIP_PUNCT = "。！!？?，, .、~～"
_EXIT_INPUT = "__exit__"  # stdin EOF 时输入线程发的哨兵


def _last_message(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "（没有返回内容）"
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    return str(content or "（没有返回内容）")


def _autogain(audio: np.ndarray, target_peak: float = 0.5) -> np.ndarray:
    """自动增益：把过低的录音峰值归一化到合理范围，便于语音识别。"""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 1e-6:
        return audio
    gain = min(target_peak / peak, 8.0)  # 最多放大 8 倍，避免爆音
    return audio * gain


def _load_vad():
    """加载 Silero VAD（faster-whisper 自带）。不可用返回 None。"""
    try:
        from faster_whisper.vad import get_vad_model

        return get_vad_model()
    except Exception:  # noqa: BLE001 - onnxruntime 缺失等
        return None


def _env_float(name: str, default: float) -> float:
    """读环境变量数值，缺省/非法回退默认值。"""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


class VoiceFeedback:
    """语音反馈门面：事件音效 + 角色音色（对标 openclaw 的 feedback 包）。

    - play(event)：播事件音效（init/listen/bargein/error/exit…）
    - voice_for_reply(reply)：从「[角色] 内容」解析出 (音色, 正文)，
      正文剥掉前缀（念「[管家]」很怪），音色按 config/voices.yaml 映射
    """

    def __init__(self, config_path: str = "config/voices.yaml") -> None:
        self.sound = SoundEngine()
        self.default_voice = "zh-CN-XiaoxiaoNeural"
        self.role_voices: dict[str, str] = {}
        try:
            root = Path(__file__).resolve().parent.parent.parent
            cfg = role_voice_config(root / config_path)
            self.default_voice = cfg["default"]
            self.role_voices = cfg["roles"]
        except Exception:  # noqa: BLE001 - 配置缺失用默认，不阻断语音
            pass

    def play(self, event: str) -> None:
        if self.sound is not None:
            self.sound.play(event)

    def voice_for_reply(self, reply: str) -> tuple[str | None, str]:
        """回复 → (本次音色, 正文)。未知角色回退默认音色。"""
        role, content = parse_role_prefix(reply)
        if role is None:
            return None, reply
        return self.role_voices.get(role, self.default_voice), content


# ── 常开麦克风流 ─────────────────────────────────────


class MicStream:
    """常开麦克风（回调入队，拼固定块长输出）。

    借鉴 openclaw 的教训：流永不 stop/close（macOS CoreAudio 上偶发永久死锁，
    Realtek 上反复开关会丢流），「丢弃输入」的语义用清空队列实现。
    """

    QUEUE_MAX = 300  # ~48s 缓冲上限，防内存无限涨

    def __init__(self, sample_rate: int = SAMPLE_RATE, device: int | None = None):
        self.sample_rate = sample_rate
        self.device = device
        self._q: queue.Queue = queue.Queue()
        self._acc: np.ndarray | None = None  # 未凑满一块的残余
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._on_frame,
        )
        self._stream.start()

    def _on_frame(self, indata, frames, time_info, status) -> None:
        if status:
            print(f"[麦克风] {status}")
        if self._q.qsize() < self.QUEUE_MAX:
            self._q.put(indata[:, 0].copy())

    def get_chunk(self, timeout: float = 0.5) -> np.ndarray:
        """取一个固定长度块（CHUNK_SAMPLES）；不足则继续等帧。"""
        while self._acc is None or self._acc.shape[0] < CHUNK_SAMPLES:
            part = self._q.get(timeout=timeout)
            self._acc = part if self._acc is None else np.concatenate([self._acc, part])
        chunk = self._acc[:CHUNK_SAMPLES]
        rest = self._acc[CHUNK_SAMPLES:]
        self._acc = rest if rest.shape[0] else None
        return chunk


# ── 话语状态机（纯逻辑，可单测） ─────────────────────


@dataclass
class Utterance:
    """一句完整的话（静音收尾或达到时长上限）。"""

    audio: np.ndarray
    interrupted: bool = False  # True = 播报中被语音打断后录到的


@dataclass
class BargeIn:
    """播报中检测到人声，已转入录音（调用方应立即停止播报）。"""


@dataclass
class NoSpeech:
    """录音超时没听到人声（背景音乐已忽略）。"""


@dataclass
class WakeWord:
    """空闲态检测到唤醒词，已转入录音（免提：紧接着直接说话即可）。"""


class UtteranceListener:
    """麦克风流 → 完整话语的状态机。

    空闲：跟踪底噪；armed（播报中）时检测语音打断（回声能量门控）。
    录音：VAD 判人声，静音 silence_duration 秒收尾；超时无人声放弃。

    灵敏度可用环境变量微调（默认偏保守，防环境声/回声误触发）：
    - CHUAN_SPEECH_PROB    常规人声判定概率（默认 0.75）
    - CHUAN_BARGEIN_PROB   打断判定概率（默认 0.9）
    - CHUAN_BARGEIN_STREAK 打断需连续块数（默认 3 ≈ 0.48s）
    - CHUAN_MIN_SPEECH     一句话最少人声块数，低于则当噪音丢弃（默认 2 ≈ 0.32s）

    线程安全：feed 在麦克风线程，start_recording/arm/disarm 在主线程。
    """

    def __init__(
        self,
        vad=None,
        silence_duration: float = 1.5,
        max_duration: float = 30.0,
        no_speech_timeout: float = 8.0,
    ):
        self.vad = vad
        self.silence_chunks = max(1, round(silence_duration / CHUNK_DUR))
        self.max_chunks = int(max_duration / CHUNK_DUR)
        self.no_speech_chunks = int(no_speech_timeout / CHUNK_DUR)
        self.speech_prob = _env_float("CHUAN_SPEECH_PROB", 0.75)
        self.bargein_prob = _env_float("CHUAN_BARGEIN_PROB", 0.9)
        self.bargein_streak = max(2, int(_env_float("CHUAN_BARGEIN_STREAK", 3)))
        self.min_speech_chunks = max(1, int(_env_float("CHUAN_MIN_SPEECH", 2)))
        self.noise_floor = 0.0015
        self.armed = False
        self.echo_rms = 0.0
        self._lock = threading.RLock()
        self._reset()

    def _reset(self) -> None:
        self._buf: list[np.ndarray] = []
        self._pre: list[np.ndarray] = []  # 空闲期最近 3 块，打断时补话语开头
        self._has_speech = False
        self._speech_chunks = 0  # 人声块计数（太少 = 噪音，丢弃）
        self._silent = 0
        self._n = 0
        self._recording = False
        self._interrupted = False
        self._streak = 0

    # ── 外部控制（主线程） ──

    def start_recording(self, interrupted: bool = False) -> None:
        with self._lock:
            pre = self._pre[-3:] if interrupted else []
            self._reset()
            self._pre = pre
            self._buf = list(pre)  # 打断路径：话语开头在触发前就已进入 _pre
            self._recording = True
            self._interrupted = interrupted
            self.armed = False

    def arm(self) -> None:
        """TTS 开始播报：开启语音打断监听。"""
        with self._lock:
            self.armed = True
            self.echo_rms = self.noise_floor
            self._streak = 0

    def disarm(self) -> None:
        with self._lock:
            self.armed = False

    @property
    def busy(self) -> bool:
        """正在录音或播报打断监听中（此期间不应做唤醒词检测）。"""
        with self._lock:
            return self._recording or self.armed

    # ── 数据流（麦克风线程） ──

    def feed(self, chunk: np.ndarray) -> "Utterance | BargeIn | NoSpeech | None":
        with self._lock:
            rms = float(np.sqrt(np.mean(chunk**2)))
            threshold = max(self.noise_floor * 3.0, 0.0015)
            prob = self._speech_prob(chunk, rms, threshold)
            if not self._recording:
                return self._feed_idle(chunk, rms, prob)
            return self._feed_recording(chunk, rms, prob)

    def _speech_prob(self, chunk: np.ndarray, rms: float, threshold: float) -> float:
        if rms <= threshold:
            return 0.0
        if self.vad is None:
            return 1.0  # 无 VAD：有能量即算人声（退化为纯 RMS）
        out = self.vad(chunk)
        return float(np.max(np.asarray(out).flatten()))

    def _feed_idle(self, chunk, rms: float, prob: float):
        # 更新底噪（仅静音块，避免人声抬高底噪）
        if rms < max(self.noise_floor * 3.0, 0.004):
            self.noise_floor = 0.9 * self.noise_floor + 0.1 * rms
        self._pre = (self._pre + [chunk])[-3:]

        if not self.armed:
            return None
        # 播报中：回声基线（慢速 EMA，用户开口不会立刻抬高基线）
        self.echo_rms = 0.9 * self.echo_rms + 0.1 * rms
        # 打断门限：明显高于回声 + 明显高于底噪 + 高 VAD 概率
        # （epsilon 容差：VAD 输出是 float32，0.9 的 float32 略小于 float64 的 0.9）
        gate = max(self.noise_floor * 6.0, self.echo_rms * 1.6)
        if prob >= self.bargein_prob - 1e-6 and rms > gate:
            self._streak += 1
        else:
            self._streak = 0
        if self._streak >= self.bargein_streak:
            self.start_recording(interrupted=True)
            return BargeIn()
        return None

    def _feed_recording(self, chunk, rms: float, prob: float):
        self._buf.append(chunk)
        self._n += 1
        if prob >= self.speech_prob - 1e-6:  # float32 容差，同上
            self._has_speech = True
            self._speech_chunks += 1
            self._silent = 0
        else:
            self._silent += 1

        if self._has_speech and self._silent >= self.silence_chunks:
            return self._finish()
        if not self._has_speech and self._n >= self.no_speech_chunks:
            self._reset()
            return NoSpeech()
        if self._n >= self.max_chunks:
            return self._finish()
        return None

    def _finish(self) -> Utterance | NoSpeech:
        # 人声块太少（键盘/桌碰等瞬态噪音）→ 当作没说话
        if self._speech_chunks < self.min_speech_chunks:
            self._reset()
            return NoSpeech()
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        interrupted = self._interrupted
        self._reset()
        return Utterance(audio=_autogain(audio), interrupted=interrupted)


# ── 后台线程 ─────────────────────────────────────────


def _input_worker(input_q: "queue.Queue[str]") -> None:
    """stdin 线程：Enter 入队。

    EOF（无终端 / Ctrl+Z/D）不退出进程——语音助手应保持常驻，
    退出走语音口令（exit/再见）或 Ctrl+C。stdin 关闭只是 Enter 兜底不可用。
    """
    while True:
        try:
            line = input()
        except (EOFError, OSError):
            print("[输入] stdin 已关闭，Enter 手动兜底不可用（语音口令退出）")
            return
        except Exception:  # noqa: BLE001
            return
        input_q.put(line)


def _maybe_wake(
    listener: UtteranceListener,
    wake_word: "WakeWordDetector | None",
    chunk: np.ndarray,
) -> bool:
    """空闲块喂唤醒词检测；命中返回 True（由主循环播应答后转录音）。

    仅在 listener 空闲时检测：录音中/播报中（busy）跳过，避免把自己
    的 TTS 回声或用户话语误判为唤醒词。detect 内部按顺序流式推理
    （openwakeword 自持滚动缓冲），块间不得重叠。
    """
    if wake_word is None or listener.busy:
        return False
    return wake_word.detect(chunk, SAMPLE_RATE)


def _mic_worker(
    mic: MicStream,
    listener: UtteranceListener,
    utterance_q: "queue.Queue",
    on_bargein: Callable[[], None],
    stop_event: threading.Event,
    wake_word: "WakeWordDetector | None" = None,
) -> None:
    """麦克风线程：取块 → 状态机 → 事件分发。"""
    empty_streak = 0
    warned = False
    while not stop_event.is_set():
        try:
            chunk = mic.get_chunk(timeout=0.2)
        except queue.Empty:
            empty_streak += 1
            if empty_streak >= 75 and not warned:  # ~15s 无帧：流可能假死
                print("[麦克风] 长时间无音频帧，请检查麦克风设备")
                warned = True
            continue
        except Exception as exc:  # noqa: BLE001 - 流崩了
            print(f"[麦克风] 读取失败: {exc}")
            return
        empty_streak = 0
        warned = False
        if _maybe_wake(listener, wake_word, chunk):
            utterance_q.put(WakeWord())
            continue  # 唤醒词块本身不进话语，直接进入录音态
        try:
            ev = listener.feed(chunk)
        except Exception:  # noqa: BLE001 - 单块分析失败不致命
            traceback.print_exc()
            continue
        if isinstance(ev, BargeIn):
            on_bargein()  # 立即停止播报
        elif ev is not None:
            utterance_q.put(ev)


def _drain_queue(q: "queue.Queue") -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


# ── 主循环（常开流模式） ─────────────────────────────


def _run_alwayson_loop(
    supervisor: RuntimeSupervisor,
    stt: STTEngine,
    tts: TTSEngine,
    mic: MicStream,
    vad,
    fb: VoiceFeedback | None = None,
    hud: HudChannel | None = None,
    wake_word: "WakeWordDetector | None" = None,
    wake_lines: list[str] | None = None,
    wake_voice: str = "zh-CN-YunjianNeural",
) -> None:
    listener = UtteranceListener(vad=vad)
    input_q: "queue.Queue[str]" = queue.Queue()
    utterance_q: "queue.Queue" = queue.Queue()
    stop_event = threading.Event()

    def on_bargein() -> None:
        tts.stop()
        if fb is not None:
            fb.play("bargein")

    threading.Thread(target=_input_worker, args=(input_q,), daemon=True).start()
    threading.Thread(
        target=_mic_worker,
        args=(mic, listener, utterance_q, on_bargein, stop_event, wake_word),
        daemon=True,
    ).start()

    tts_thread: dict[str, Any] = {"t": None}
    prompt_shown = False
    awake_pending = False  # 唤醒应答播报中：播完自动转录音

    def playback_done() -> bool:
        t = tts_thread["t"]
        if t is not None and not t.is_alive():
            tts_thread["t"] = None
            listener.disarm()
            return True
        return t is None

    def goodbye() -> None:
        tts.stop()
        if fb is not None:
            fb.play("exit")
        print("再见。")

    try:
        while True:
            # ── 阶段 1：等一句话的开始（唤醒词 / Enter，或播报中开口打断）──
            utterance: Utterance | None = None
            while utterance is None:
                if awake_pending and playback_done():
                    # 唤醒应答播完 → 自动转录音，请讲
                    awake_pending = False
                    listener.start_recording()
                    print("✓ 请讲…")
                    break
                if playback_done() and not prompt_shown:
                    if wake_word is not None:
                        print(f"\n[等待唤醒] 说「{wake_word.prompt}」后直接讲（Enter 可手动）…")
                    else:
                        print("\n[按 Enter 说话…]")
                    prompt_shown = True
                try:
                    line = input_q.get_nowait()
                except queue.Empty:
                    line = None
                if line == _EXIT_INPUT:
                    goodbye()
                    return
                if line is not None:  # Enter（顺带打断还在播的上一句）
                    tts.stop()
                    prompt_shown = False
                    if fb is not None:
                        fb.play("listen")
                    listener.start_recording()
                    _drain_queue(utterance_q)  # 丢弃打断产生的旧话语
                    break
                try:
                    ev = utterance_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if isinstance(ev, Utterance):
                    utterance = ev  # 语音打断直接产出完整话语
                    awake_pending = False  # 打断应答后的话语优先
                    prompt_shown = False
                elif isinstance(ev, WakeWord):
                    # 免提唤醒：专属音色自动应答（播报中开口可打断）
                    tts.stop()
                    prompt_shown = True  # 抑制等待期间重复打印提示
                    if fb is not None:
                        fb.play("listen")
                    if hud is not None:
                        hud.wake()
                    print("✓ 已唤醒")
                    line_text = _random_wake_line(wake_lines or [])
                    print(f"川流：{line_text}")
                    if hud is not None:
                        hud.show_ai_text(line_text)
                    t = tts.speak_async(line_text, voice=wake_voice, rate="+15%", metallic=True)
                    listener.arm()  # 应答播报中开口 = 打断并直接录话语
                    awake_pending = True
                    if t is None:  # TTS 不可用：跳过应答直接录
                        listener.disarm()
                        awake_pending = False
                        listener.start_recording()
                        print("✓ 请讲…")
                        break

            # ── 阶段 2：等录音收尾（Enter 触发的路径）──
            if utterance is None:
                while True:
                    try:
                        ev = utterance_q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if isinstance(ev, Utterance):
                        utterance = ev
                        break
                    if isinstance(ev, NoSpeech):
                        print("（未听到人声——背景音乐已忽略）")
                        prompt_shown = False  # 回到等待态重新提示
                        break
                if utterance is None:
                    continue

            if utterance.audio.size == 0:
                continue

            # ── 阶段 3：识别 → 派发 → 播报 ──
            print("正在识别…")
            text = stt.transcribe(utterance.audio)
            if not text:
                print("（未识别到语音）")
                continue
            print(f"你说：{text}")

            # 打断录到的可能是播报回声/碎字：太短直接丢弃
            if utterance.interrupted and len(text.strip(_STRIP_PUNCT)) < 2:
                continue
            # 退出口令：剥掉标点再匹配（转写常带「退出。」「再见！」）
            if text.lower().strip(_STRIP_PUNCT) in _EXIT_WORDS:
                if fb is not None:
                    fb.play("exit")
                tts.speak("再见。")
                print("再见。")
                return

            try:
                if fb is not None:
                    fb.play("thinking")  # LLM 思考期给个轻提示，避免静默
                result = supervisor.dispatch(text)
            except Exception:  # noqa: BLE001
                if fb is not None:
                    fb.play("error")
                raise
            reply = _last_message(result)
            print(f"川流：{reply}")

            if hud is not None:
                hud.show_user_text(text)
                hud.effect("speaking")
                hud.show_ai_text(reply)
                push_monitor_snapshot(supervisor, hud)

            # 按角色换音色播报（正文剥掉「[角色]」前缀；长回复只读前 500 字）
            voice, speak_text = (
                fb.voice_for_reply(reply) if fb is not None else (None, reply)
            )
            t = tts.speak_async(speak_text[:500], voice=voice)
            tts_thread["t"] = t
            prompt_shown = False
            if t is not None:  # 空回复没有播报，不武装打断
                listener.arm()
                print("（播报中…开口说话或按 Enter 打断）")

    except KeyboardInterrupt:
        goodbye()
    finally:
        stop_event.set()


# ── 旧模式：阻塞录音（唤醒词 / 常开流启动失败时的回退） ──


def record_audio(
    sample_rate: int = SAMPLE_RATE,
    silence_threshold: float | None = None,
    silence_duration: float = 1.5,
    max_duration: float = 30.0,
    device: int | None = None,
) -> np.ndarray:
    """阻塞式录音（按 Enter 后调用），静音自动停止。

    用「一次持续录音 + 逐块分析」而非反复 sd.rec/sd.wait——Realtek 等声卡
    对小块 rec 循环会丢流。人声判定与常开流一致（Silero VAD 优先）。
    """
    import sounddevice as sd

    vad = _load_vad()
    chunk_duration = 0.16
    chunk_samples = int(sample_rate * chunk_duration)
    silence_chunks = int(silence_duration / chunk_duration)
    no_speech_limit = int(8.0 / chunk_duration)

    # 采集 0.3s 环境底噪，估算自适应阈值
    if silence_threshold is None:
        baseline = sd.rec(
            int(sample_rate * 0.3), samplerate=sample_rate, channels=1, dtype=np.float32,
            device=device,
        )
        sd.wait()
        noise_rms = float(np.sqrt(np.mean(baseline**2)))
        silence_threshold = max(noise_rms * 3.0, 0.0015)
        print(f"[录音] 底噪 RMS={noise_rms:.5f}，自适应阈值={silence_threshold:.5f}")

    stream = sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="float32", device=device
    )
    stream.start()

    audio_chunks: list[np.ndarray] = []
    silent_count = 0
    has_speech = False
    n_chunks = int(max_duration / chunk_duration)

    try:
        for i in range(n_chunks):
            data, _ = stream.read(chunk_samples)
            data = data.flatten()
            rms = float(np.sqrt(np.mean(data**2)))
            audio_chunks.append(data)

            if rms > silence_threshold and vad is not None:
                probs = vad(data)  # type: ignore[misc]
                is_speech = float(np.max(np.asarray(probs).flatten())) >= 0.6
            elif rms > silence_threshold:
                is_speech = True  # 无 VAD：有能量即算人声
            else:
                is_speech = False

            if is_speech:
                has_speech = True
                silent_count = 0
            else:
                silent_count += 1

            if has_speech and silent_count >= silence_chunks:
                break  # 说过话后静音 → 说完了
            if not has_speech and i + 1 >= no_speech_limit:
                print("[录音] 未听到人声（背景音乐已忽略）")
                audio_chunks = []
                break
    finally:
        stream.stop()
        stream.close()

    if not audio_chunks:
        return np.array([])

    audio = np.concatenate(audio_chunks).flatten()
    return _autogain(audio)


def _run_enter_loop(
    supervisor: RuntimeSupervisor,
    stt: STTEngine,
    tts: TTSEngine,
    wake_word: WakeWordDetector | None,
    fb: VoiceFeedback | None = None,
    hud: HudChannel | None = None,
) -> None:
    """旧流程：唤醒词（可选）→ Enter → 阻塞录音 → 识别 → 派发。"""
    use_wake_word = wake_word is not None
    print("幕僚长已就绪。说 exit 退出。\n")

    while True:
        try:
            if use_wake_word and wake_word is not None:
                print(f"[等待唤醒] 说「{wake_word.prompt}」…")
                from chuan.voice.wake_word import record_until_wake

                record_until_wake(wake_word, SAMPLE_RATE)

            input("[按 Enter 说话…]")
            # 打断上一轮可能还在播的回复（先停播再录音，避免录进川流自己的声音）
            tts.stop()
            if fb is not None:
                fb.play("listen")

            print("🎤 正在录音（说完停顿自动结束）…")
            audio = record_audio()
            if len(audio) == 0:
                print("（未录到音频）")
                continue

            print("正在识别…")
            text = stt.transcribe(audio)
            if not text:
                print("（未识别到语音）")
                continue

            print(f"你说：{text}")

            if text.lower().strip(_STRIP_PUNCT) in _EXIT_WORDS:
                if fb is not None:
                    fb.play("exit")
                tts.speak("再见。")
                print("再见。")
                break

            try:
                if fb is not None:
                    fb.play("thinking")  # LLM 思考期给个轻提示，避免静默
                result = supervisor.dispatch(text)
            except Exception:  # noqa: BLE001
                if fb is not None:
                    fb.play("error")
                raise
            reply = _last_message(result)
            print(f"川流：{reply}")

            if hud is not None:
                hud.show_user_text(text)
                hud.effect("speaking")
                hud.show_ai_text(reply)
                push_monitor_snapshot(supervisor, hud)

            # 后台播报（按角色音色；想打断随时按 Enter）
            voice, speak_text = (
                fb.voice_for_reply(reply) if fb is not None else (None, reply)
            )
            tts.speak_async(speak_text[:500], voice=voice)
            print("（播报中…按 Enter 打断）")

        except KeyboardInterrupt:
            tts.stop()
            if fb is not None:
                fb.play("exit")
            print("\n再见。")
            break
        except Exception as exc:  # noqa: BLE001 - keep voice loop alive
            msg = str(exc).lower()
            kind = "LLM 接口" if any(
                k in msg for k in ("api", "connection attempts", "timeout", "http", "zhipu", "openapi", "ollama")
            ) else ("MCP 工具" if "mcp" in msg or "server" in msg else "其他")
            print(f"[错误 · {kind}] {exc}")
            traceback.print_exc()


# ── 入口 ─────────────────────────────────────────────


def _wake_greeting(
    config_path: Path | None = None,
) -> tuple[list[str], str]:
    """读唤醒应答配置：(随机话术列表, 应答音色)。读不到给内置默认。"""
    default_lines = ["在的，先生。请讲。", "我在听，先生。"]
    default_voice = "zh-CN-YunjianNeural"
    try:
        import yaml

        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
        if not config_path.exists():
            return default_lines, default_voice
        with config_path.open("r", encoding="utf-8") as f:
            sec = (yaml.safe_load(f) or {}).get("wakeword") or {}
        lines = [str(x) for x in sec.get("wake_lines") or [] if str(x).strip()]
        voice = str(sec.get("wake_voice") or default_voice)
        return (lines or default_lines), voice
    except Exception:  # noqa: BLE001
        return default_lines, default_voice


def _random_wake_line(lines: list[str]) -> str:
    """随机挑一条唤醒应答话术。"""
    return random.choice(lines) if lines else "在的，先生。请讲。"


def _resolve_wake_word(
    wake_word: WakeWordDetector | None,
    config_path: Path | None = None,
) -> WakeWordDetector | None:
    """确定醒词检测器。

    显式传入的醒词检测器优先；否则读 config.yaml 的 wakeword.enabled/phrase。
    未启用或读取失败返回 None（走「按 Enter 说话」模式）。
    """
    if wake_word is not None:
        return wake_word
    try:
        import yaml

        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
        if not config_path.exists():
            return None
        with config_path.open("r", encoding="utf-8") as f:
            sec = (yaml.safe_load(f) or {}).get("wakeword") or {}
        if not sec.get("enabled"):
            return None
        return WakeWordDetector(keyword=str(sec.get("phrase", "小川小川")))
    except Exception:  # noqa: BLE001 - 读配置失败直接回退按键模式
        return None


def run_voice_mode(
    *,
    supervisor_factory: Any = RuntimeSupervisor,
    stt: STTEngine | None = None,
    tts: TTSEngine | None = None,
    wake_word: WakeWordDetector | None = None,
) -> None:
    """运行语音交互循环。"""
    print("川流 chuan-os v0.1.0 [语音模式]")
    print("幕僚长正在醒来…")

    supervisor = supervisor_factory()
    supervisor.wake_up()

    if stt is None:
        stt = STTEngine()
    if tts is None:
        tts = TTSEngine()

    wake_word = _resolve_wake_word(wake_word)

    print("正在加载语音模型…")
    try:
        stt.load()
        print(f"[STT] {stt.backend} 已就绪")
    except ImportError as e:
        print(f"[STT] 加载失败: {e}")
        supervisor.shutdown()
        return

    vad = _load_vad()
    if vad is not None:
        print("[VAD] Silero 人声判定已就绪（音乐/环境声不触发）")

    use_wake_word = False
    if wake_word is not None and wake_word.load():
        use_wake_word = True
        print(f"[唤醒词] openwakeword 已就绪，说「{wake_word.prompt}」开始对话")

    fb = VoiceFeedback()
    fb.play("init")
    if fb.sound is not None:
        print("[音效] 事件提示已开启（CHUAN_SOUNDS=0 可关闭）")

    hud: HudChannel | None = None
    try:
        candidate = HudChannel()
        if candidate.alive:
            candidate.wake()
            # N34 SCENE 协议握手：连接后推 hello（caps 协商）+ 全量 scene
            if getattr(candidate, "scene_enabled", False):
                candidate.send_hello()
                candidate.send_scene_full()
            hud = candidate
            print(f"[HUD] Jarvis 全息悬浮层在线（{hud.endpoint}），对话将实时驱动")
    except Exception:  # noqa: BLE001 - HUD 不可用不影响语音
        hud = None

    try:
        # 常开流模式：唤醒词（免提）与 Enter 手动可并存
        try:
            mic = MicStream()
            mic.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[麦克风] 常开流启动失败（{exc}），回退按键模式")
            _run_enter_loop(supervisor, stt, tts, None, fb, hud)
            return

        if use_wake_word:
            wake_lines, wake_voice = _wake_greeting()
            print("[麦克风] 常开监听已启动（说唤醒词后直接讲；Enter 手动兜底）")
        else:
            wake_lines, wake_voice = None, "zh-CN-YunjianNeural"
            print("[麦克风] 常开监听已启动（按 Enter 说话；播报时开口即可打断）")
        _run_alwayson_loop(
            supervisor, stt, tts, mic, vad, fb, hud, wake_word, wake_lines, wake_voice
        )
    finally:
        supervisor.shutdown()
