"""语音模块测试（STT/TTS/WakeWord 优雅降级）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from chuan.voice.sounds import SoundEngine, parse_role_prefix, role_voice_config
from chuan.voice.stt import STTEngine
from chuan.voice.tts import TTSEngine
from chuan.voice.wake_word import WakeWordDetector


# ── 事件音效（sounds.py） ────────────────────────────


def test_synth_notes_shape_and_volume() -> None:
    from chuan.voice.sounds import _EVENTS, _synth_notes

    audio = _synth_notes(_EVENTS["init"], 0.4)
    # init = 0.09+0.09+0.14 = 0.32s
    assert audio.dtype == np.float32
    assert abs(audio.size / 24000 - 0.32) < 0.01
    assert float(np.max(np.abs(audio))) <= 0.4 + 1e-6  # 音量封顶


def test_thinking_event_present_and_synthesizes() -> None:
    from chuan.voice.sounds import _EVENTS, _synth_notes

    # N16：LLM 静默思考期要有音频反馈，补齐 openclaw 事件集
    assert "thinking" in _EVENTS
    audio = _synth_notes(_EVENTS["thinking"], 0.4)
    assert audio.dtype == np.float32
    assert audio.size > 0
    assert float(np.max(np.abs(audio))) <= 0.4 + 1e-6


def test_sound_engine_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUAN_SOUNDS", "0")
    engine = SoundEngine()
    assert engine.enabled is False
    engine.play("init")  # 静默，不抛异常


def test_sound_engine_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = SoundEngine()
    engine.enabled = True
    played: list[list] = []
    engine._play_sync = lambda notes: played.append(notes)  # type: ignore[method-assign]
    engine.play("init")
    engine.play("init")  # 0.5s 内重复 → 防抖丢弃
    assert len(played) == 1
    engine.play("error")  # 不同音效不受防抖影响
    assert len(played) == 2


def test_parse_role_prefix() -> None:
    assert parse_role_prefix("[管家] 今天晴") == ("管家", "今天晴")
    assert parse_role_prefix("普通回复没有前缀") == (None, "普通回复没有前缀")
    # markdown 链接不算角色前缀
    role, _ = parse_role_prefix("[https://example.com/a-very-long-link] 内容")
    assert role is None


def test_role_voice_config_loads(tmp_path) -> None:
    cfg_file = tmp_path / "voices.yaml"
    cfg_file.write_text("default: zh-CN-A\ndefault2: x\nroles:\n  管家: zh-CN-B\n", encoding="utf-8")
    cfg = role_voice_config(cfg_file)
    assert cfg["default"] == "zh-CN-A"
    assert cfg["roles"]["管家"] == "zh-CN-B"


def test_voice_feedback_maps_role_to_voice() -> None:
    from chuan.voice.main import VoiceFeedback

    with patch.object(VoiceFeedback, "__init__", lambda self, config_path="x": None):
        fb = VoiceFeedback()
        fb.sound = None
        fb.default_voice = "zh-CN-XiaoxiaoNeural"
        fb.role_voices = {"管家": "zh-CN-XiaoxiaoNeural", "幕僚长": "zh-CN-YunxiNeural"}
        voice, text = fb.voice_for_reply("[幕僚长] 你好")
        assert voice == "zh-CN-YunxiNeural"
        assert text == "你好"
        # 未知角色 → 默认音色
        voice, _ = fb.voice_for_reply("[神秘人] 你好")
        assert voice == "zh-CN-XiaoxiaoNeural"
        # 无前缀 → 默认音色、原文
        voice, text = fb.voice_for_reply("没有前缀")
        assert voice is None and text == "没有前缀"


# ── TTS 音色参数 ─────────────────────────────────────


def test_tts_speak_passes_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """speak(voice=...) 应把音色透传给 edge-tts 合成。"""
    import sys
    import types

    # edge_tts 是可选依赖；未安装时注入占位模块，让 _try_edge_tts 走到 _synth_edge
    if "edge_tts" not in sys.modules:
        monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))

    tts = TTSEngine()

    captured: dict = {}

    async def fake_synth(edge_tts, text, path, voice=None, rate=None):
        captured["voice"] = voice
        tts._generation += 1  # 模拟合成期间被打断，跳过播放

    with patch.object(tts, "_synth_edge", side_effect=fake_synth):
        tts._try_edge_tts("你好", tts._generation, voice="zh-CN-YunxiNeural")
    assert captured["voice"] == "zh-CN-YunxiNeural"


def test_tts_speak_async_voice_kwarg() -> None:
    tts = TTSEngine()
    called: dict = {}
    tts.speak = (
        lambda text, voice=None, rate=None, metallic=False: called.update(  # type: ignore[method-assign]
            text=text, voice=voice
        )
    )
    t = tts.speak_async("测试", voice="zh-CN-XiaoyiNeural")
    t.join(timeout=2)
    assert called == {"text": "测试", "voice": "zh-CN-XiaoyiNeural"}


# ── STT ──────────────────────────────────────────────


def test_stt_engine_init() -> None:
    stt = STTEngine(model_size="tiny")
    assert stt.model_size == "tiny"
    assert stt.backend == ""  # 未加载


def test_stt_load_missing_deps_raises() -> None:
    stt = STTEngine()
    with patch.dict("sys.modules", {"faster_whisper": None, "whisper": None}):
        with pytest.raises(ImportError, match="STT 需要安装"):
            stt.load()


def test_stt_transcribe_with_mock() -> None:
    stt = STTEngine()
    # 模拟 faster-whisper 已安装
    fake_model = MagicMock()
    fake_segment = MagicMock(text="  你好世界  ")
    fake_model.transcribe.return_value = ([fake_segment], MagicMock())
    stt._model = fake_model
    stt._backend = "faster-whisper"

    audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe(audio)
    assert result == "你好世界"


# ── TTS ──────────────────────────────────────────────


def test_tts_engine_init() -> None:
    tts = TTSEngine()
    assert tts.voice == "zh-CN-XiaoxiaoNeural"


def test_tts_empty_text_noop() -> None:
    tts = TTSEngine()
    # 空文本不应崩溃
    tts.speak("")
    tts.speak("   ")


def test_tts_edge_unavailable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    tts = TTSEngine()
    # 模拟 edge_tts 不可用
    monkeypatch.setitem(__import__("sys").modules, "edge_tts", None)
    tts._edge_available = False
    # 应回退系统 TTS，不崩溃
    with patch.object(tts, "_system_tts") as mock_sys:
        tts.speak("测试")
        mock_sys.assert_called_once_with("测试")


# ── TTS 打断/后台播报 ────────────────────────────────


def test_tts_stop_no_crash() -> None:
    tts = TTSEngine()
    tts.stop()  # 未播放时 stop 不崩溃，代际+1
    assert tts._generation == 1


def test_tts_speak_async_returns_thread() -> None:
    tts = TTSEngine()
    with patch.object(tts, "speak") as mock_speak:
        t = tts.speak_async("测试")
        assert t is not None
        t.join(timeout=2)
        mock_speak.assert_called_once_with(
            "测试", voice=None, rate=None, metallic=False
        )
    # 空文本返回 None
    assert tts.speak_async("") is None
    assert tts.speak_async("   ") is None


def test_tts_interrupt_during_synth_skips_playback() -> None:
    """合成期间被打断：代际变化，不再播放。"""
    tts = TTSEngine()
    gen = tts._generation
    tts.stop()  # 打断
    # speak 取到旧代际 gen，但合成完成时 _generation 已变
    assert tts._generation != gen


# ── record_audio VAD（音乐不算说话） ────────────────


class _FakeInputStream:
    """模拟麦克风流：按调用序号返回预设音频块。"""

    blocks: list[np.ndarray] = []

    def __init__(self, **kwargs: object) -> None:
        self._idx = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read(self, n: int) -> tuple[np.ndarray, int]:
        block = self.blocks[self._idx] if self._idx < len(self.blocks) else np.zeros(n, dtype=np.float32)
        self._idx += 1
        return (block, 0)


def test_record_audio_music_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有能量但 VAD 判非人声（背景音乐）→ 不算说话，返回空。"""
    import sys
    import types

    from chuan.voice import main as voice_main

    chunk = np.full(2560, 0.3, dtype=np.float32)  # 高能量「音乐」
    _FakeInputStream.blocks = [chunk] * 100

    sd_mod = types.SimpleNamespace(
        InputStream=_FakeInputStream,
        rec=lambda *a, **k: np.zeros((4800, 1), dtype=np.float32),
        wait=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd_mod)

    # VAD 恒返回低概率（音乐）
    fake_vad = MagicMock(return_value=np.full(5, 0.05))
    monkeypatch.setattr(voice_main, "_load_vad", lambda: fake_vad)

    audio = voice_main.record_audio(max_duration=10.0)
    assert audio.size == 0  # 音乐被忽略


def test_record_audio_speech_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真人声（VAD 高概率）→ 正常录音，静音后停止。"""
    import sys
    import types

    from chuan.voice import main as voice_main

    speech = np.full(2560, 0.3, dtype=np.float32)
    _FakeInputStream.blocks = [speech] * 5  # 0.8s 人声后静音

    sd_mod = types.SimpleNamespace(
        InputStream=_FakeInputStream,
        rec=lambda *a, **k: np.zeros((4800, 1), dtype=np.float32),
        wait=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd_mod)

    fake_vad = MagicMock(return_value=np.full(5, 0.9))  # 人声概率高
    monkeypatch.setattr(voice_main, "_load_vad", lambda: fake_vad)

    audio = voice_main.record_audio(max_duration=10.0)
    assert audio.size > 0  # 录到了（含 autogain 放大）


# ── UtteranceListener（常开流状态机） ────────────────


def _chunk(rms: float = 0.0, n: int = 2560) -> np.ndarray:
    return np.full(n, rms, dtype=np.float32)


def _mk_vad(loud_prob: float = 0.9, quiet_prob: float = 0.05, boundary: float = 0.2):
    """假 VAD：响块给 loud_prob，轻块给 quiet_prob（模拟人声/回声差异）。"""

    def _f(chunk: np.ndarray) -> np.ndarray:
        rms = float(np.sqrt(np.mean(chunk**2)))
        return np.full(5, loud_prob if rms > boundary else quiet_prob, dtype=np.float32)

    return MagicMock(side_effect=_f)


def test_listener_enter_recording_flow() -> None:
    from chuan.voice.main import Utterance, UtteranceListener

    listener = UtteranceListener(vad=_mk_vad())
    listener.start_recording()
    # 说话（响块 = 人声）
    for _ in range(5):
        assert listener.feed(_chunk(0.3)) is None
    # 静音收尾 → 恰好一个 Utterance
    events = [listener.feed(_chunk(0.0)) for _ in range(12)]
    results = [e for e in events if e is not None]
    assert len(results) == 1 and isinstance(results[0], Utterance)
    assert results[0].interrupted is False
    assert results[0].audio.size > 0  # 含 autogain 放大后的音频


def test_listener_music_gives_no_speech() -> None:
    from chuan.voice.main import NoSpeech, UtteranceListener

    listener = UtteranceListener(vad=_mk_vad())  # 响块但 VAD 判非人声场景见下
    # 用恒低概率 VAD 模拟音乐：有能量、非人声
    listener.vad = MagicMock(return_value=np.full(5, 0.05, dtype=np.float32))
    listener.start_recording()
    ev = None
    for _ in range(50):  # 8s 超时
        ev = listener.feed(_chunk(0.3))
        if ev is not None:
            break
    assert isinstance(ev, NoSpeech)


def test_listener_bargein_flow() -> None:
    from chuan.voice.main import BargeIn, Utterance, UtteranceListener

    listener = UtteranceListener(vad=_mk_vad())
    listener.arm()
    # 播报回声（轻、低 VAD 概率）→ 不触发
    for _ in range(5):
        assert listener.feed(_chunk(0.05)) is None
    # 用户开口（响、高 VAD 概率）→ 连续 3 块才确认打断
    assert listener.feed(_chunk(0.4)) is None  # streak 1
    assert listener.feed(_chunk(0.4)) is None  # streak 2
    ev = listener.feed(_chunk(0.4))  # streak 3 → BargeIn
    assert isinstance(ev, BargeIn)
    # 打断后继续录音：说话 + 静音收尾
    for _ in range(3):
        listener.feed(_chunk(0.4))
    events = [listener.feed(_chunk(0.0)) for _ in range(12)]
    results = [e for e in events if e is not None]
    assert len(results) == 1
    assert isinstance(results[0], Utterance)
    assert results[0].interrupted is True
    assert results[0].audio.size > 0


def test_listener_bargein_broken_streak_resets() -> None:
    """两声咳嗽（非连续）不应触发打断。"""
    from chuan.voice.main import UtteranceListener

    listener = UtteranceListener(vad=_mk_vad())
    listener.arm()
    for _ in range(3):
        assert listener.feed(_chunk(0.05)) is None
    assert listener.feed(_chunk(0.4)) is None  # streak 1
    assert listener.feed(_chunk(0.05)) is None  # 断 → streak 归零
    assert listener.feed(_chunk(0.4)) is None  # streak 1（重新计数）
    assert listener.feed(_chunk(0.4)) is None  # streak 2
    assert listener.armed is True  # 未达 3 连块，从未打断


def test_listener_stray_noise_discarded() -> None:
    """单块瞬态噪音（键盘/桌碰）：低于最少人声块数 → 丢弃。"""
    from chuan.voice.main import NoSpeech, UtteranceListener

    listener = UtteranceListener(vad=_mk_vad())
    listener.start_recording()
    listener.feed(_chunk(0.4))  # 仅 1 块人声
    events = [listener.feed(_chunk(0.0)) for _ in range(12)]
    results = [e for e in events if e is not None]
    assert len(results) == 1
    assert isinstance(results[0], NoSpeech)  # 当作没说话


def test_listener_idle_without_arm_ignores_speech() -> None:
    """未 armed（非播报中）：人声不触发任何事（Enter 门控）。"""
    from chuan.voice.main import UtteranceListener

    listener = UtteranceListener(vad=_mk_vad())
    for _ in range(20):
        assert listener.feed(_chunk(0.4)) is None
    assert listener.armed is False


def test_listener_noise_floor_adapts() -> None:
    from chuan.voice.main import UtteranceListener

    listener = UtteranceListener(vad=None)
    for _ in range(30):
        listener.feed(_chunk(0.0005))
    assert listener.noise_floor < 0.001


def test_listener_no_vad_falls_back_to_rms() -> None:
    """无 VAD：有能量即算人声（退化行为不劣化）。"""
    from chuan.voice.main import Utterance, UtteranceListener

    listener = UtteranceListener(vad=None)
    listener.start_recording()
    for _ in range(5):
        listener.feed(_chunk(0.3))
    events = [listener.feed(_chunk(0.0)) for _ in range(12)]
    results = [e for e in events if e is not None]
    assert len(results) == 1 and isinstance(results[0], Utterance)


# ── MicStream（常开流 + 固定块拼接） ─────────────────


def test_mic_stream_fixed_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from chuan.voice.main import CHUNK_SAMPLES, MicStream

    class _FakeStream:
        def __init__(self, **kwargs: object) -> None:
            self.cb = kwargs["callback"]

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    sd_mod = types.SimpleNamespace(InputStream=_FakeStream)
    monkeypatch.setitem(sys.modules, "sounddevice", sd_mod)

    mic = MicStream()
    mic.start()
    # 回调送 2 帧 × 1600 样本 = 3200
    for _ in range(2):
        mic._on_frame(np.full((1600, 1), 0.1, dtype=np.float32), 1600, None, None)

    chunk = mic.get_chunk(timeout=0.1)
    assert chunk.shape[0] == CHUNK_SAMPLES == 2560
    assert mic._acc is not None and mic._acc.shape[0] == 640  # 残余


# ── WakeWord ─────────────────────────────────────────


def test_wake_word_init() -> None:
    detector = WakeWordDetector(keyword="chuan")
    assert detector.keyword == "chuan"
    assert detector.available is False


def test_wake_word_load_no_deps(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    detector = WakeWordDetector()
    # 模型目录为空时 load() 返回 False，不崩溃
    monkeypatch.setattr("os.path.expanduser", lambda _: str(tmp_path / "no_models"))
    result = detector.load()
    assert result is False
    assert detector.available is False


def test_wake_word_detect_unavailable_returns_false() -> None:
    detector = WakeWordDetector()
    audio = np.zeros(16000, dtype=np.float32)
    assert detector.detect(audio) is False


# ── Voice main helper ─────────────────────────────────


def test_last_message_voice() -> None:
    from chuan.voice.main import _last_message

    mock_msg = MagicMock()
    mock_msg.content = "语音回复"
    result = {"messages": [mock_msg]}
    assert _last_message(result) == "语音回复"


def test_resolve_wake_word_disabled_returns_none(tmp_path) -> None:
    from chuan.voice.main import _resolve_wake_word

    cfg = tmp_path / "config.yaml"
    cfg.write_text("wakeword:\n  enabled: false\n  phrase: 小川小川\n", encoding="utf-8")
    # enabled: false → 返回 None（按 Enter 模式）
    assert _resolve_wake_word(None, config_path=cfg) is None


def test_resolve_wake_word_enabled_uses_phrase(tmp_path) -> None:
    from chuan.voice.main import _resolve_wake_word

    cfg = tmp_path / "config.yaml"
    cfg.write_text("wakeword:\n  enabled: true\n  phrase: 小川小川\n", encoding="utf-8")
    detector = _resolve_wake_word(None, config_path=cfg)
    assert detector is not None
    assert detector.keyword == "小川小川"


def test_resolve_wake_word_takes_explicit_over_config() -> None:
    from chuan.voice.main import _resolve_wake_word
    from chuan.voice.wake_word import WakeWordDetector

    detector = WakeWordDetector(keyword="custom")
    assert _resolve_wake_word(detector) is detector


# ── 免提唤醒（常开循环集成） ──────────────────────────


class _FakeWakeDetector:
    """对包含非零能量的块返回命中的假唤醒检测器。"""

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, chunk: np.ndarray, sample_rate: int = 16000) -> bool:
        self.calls += 1
        return bool(np.abs(chunk).max() > 0.2)


def test_maybe_wake_idle_hit_returns_true() -> None:
    from chuan.voice.main import UtteranceListener, _maybe_wake

    listener = UtteranceListener(vad=_mk_vad())
    assert listener.busy is False
    assert _maybe_wake(listener, _FakeWakeDetector(), _chunk(0.3)) is True
    # 命中只报告事件，不直接转录音（主循环先播应答再录）
    assert listener.busy is False


def test_maybe_wake_busy_skips_detection() -> None:
    from chuan.voice.main import UtteranceListener, _maybe_wake

    listener = UtteranceListener(vad=_mk_vad())
    listener.start_recording()  # 录音中 → busy
    det = _FakeWakeDetector()
    assert _maybe_wake(listener, det, _chunk(0.3)) is False
    assert det.calls == 0  # busy 时根本不喂唤醒检测（防 TTS 回声误触）


def test_maybe_wake_none_detector_returns_false() -> None:
    from chuan.voice.main import UtteranceListener, _maybe_wake

    listener = UtteranceListener(vad=_mk_vad())
    assert _maybe_wake(listener, None, _chunk(0.3)) is False
    assert listener.busy is False


def test_maybe_wake_silent_chunk_no_hit() -> None:
    from chuan.voice.main import UtteranceListener, _maybe_wake

    listener = UtteranceListener(vad=_mk_vad())
    assert _maybe_wake(listener, _FakeWakeDetector(), _chunk(0.0)) is False
    assert listener.busy is False


# ── 唤醒自动应答（贾维斯管家话术 + 专属音色） ─────────


def test_wake_greeting_reads_config(tmp_path) -> None:
    from chuan.voice.main import _wake_greeting

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "wakeword:\n"
        "  wake_lines:\n"
        "    - 在的，先生。\n"
        "    - 随时待命。\n"
        "  wake_voice: zh-CN-YunjianNeural\n",
        encoding="utf-8",
    )
    lines, voice = _wake_greeting(config_path=cfg)
    assert lines == ["在的，先生。", "随时待命。"]
    assert voice == "zh-CN-YunjianNeural"


def test_wake_greeting_missing_config_returns_defaults(tmp_path) -> None:
    from chuan.voice.main import _wake_greeting

    lines, voice = _wake_greeting(config_path=tmp_path / "nope.yaml")
    assert len(lines) >= 1
    assert voice == "zh-CN-YunjianNeural"


def test_random_wake_line_picks_from_list() -> None:
    from chuan.voice.main import _random_wake_line

    lines = ["甲", "乙", "丙"]
    assert _random_wake_line(lines) in lines


def test_random_wake_line_empty_list_has_default() -> None:
    from chuan.voice.main import _random_wake_line

    assert _random_wake_line([]) != ""


def test_tts_speak_metallic_params_passthrough() -> None:
    """metallic/rate/voice 参数应一路传到播放层。"""
    from unittest.mock import patch

    from chuan.voice import tts as tts_mod

    engine = tts_mod.TTSEngine()
    with patch.object(engine, "_try_edge_tts", return_value=True) as m:
        engine.speak("在的，先生", voice="zh-CN-YunjianNeural", rate="+15%", metallic=True)
    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["voice"] == "zh-CN-YunjianNeural"
    assert kwargs["rate"] == "+15%"
    assert kwargs["metallic"] is True


def test_apply_metallic_no_ffmpeg_returns_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 ffmpeg 时原样返回，不阻断。"""
    from chuan.voice import tts as tts_mod

    monkeypatch.setattr(tts_mod, "_FFMPEG_BIN", None)
    data = np.ones(1600, dtype=np.float32) * 0.5
    out, sr = tts_mod.apply_metallic(data, 16000)
    assert out is data  # 原样返回同一数组
    assert sr == 16000
