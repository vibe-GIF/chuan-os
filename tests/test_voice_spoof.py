"""声纹防欺骗（anti-spoof）V1 测试（chuan/voice/spoof.py）。"""

from __future__ import annotations

import numpy as np
import pytest

from chuan.voice import spoof


def _sine(dur: float = 2.0, freq: float = 180.0, amp: float = 0.3, sr: int = 16000) -> np.ndarray:
    """合成人声样（正弦 + 汉宁包络）。"""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    envelope = np.hanning(int(sr * dur))
    sig = amp * envelope * np.sin(2 * np.pi * freq * t)
    return sig.astype(np.float32)


def _flat_tone(dur: float = 2.0, freq: float = 600.0, amp: float = 0.01, sr: int = 16000) -> np.ndarray:
    """另一路「声」：无包络的平直正弦（能量轮廓/尺度与 _sine 明显不同）。"""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


@pytest.fixture
def speaker_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> object:
    """把注册目录指向临时目录，避免污染真实 data/speakers。"""
    d = tmp_path / "speakers"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(spoof, "_SPEAKERS_DIR", d)
    return d


# ── enroll 写盘 + 读回 ──────────────────────────────


def test_enroll_writes_json_and_load_back(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine()) is True
    path = speaker_dir / "alice.json"
    assert path.exists()
    feat = spoof.load_speaker("alice")
    assert feat is not None
    assert feat["duration_s"] > 1.5
    assert len(feat["vector"]) == spoof.PROFILE_POINTS


def test_enroll_rejects_silence_or_too_short(speaker_dir) -> None:
    assert spoof.enroll_speaker("silent", np.zeros(16000, dtype=np.float32)) is False
    assert spoof.enroll_speaker("short", _sine(dur=0.2)) is False
    assert not (speaker_dir / "silent.json").exists()


def test_enroll_rejects_unsafe_name(speaker_dir) -> None:
    """含路径分隔符 /「..」的名 → 拒绝，绝不写出目录。"""
    assert spoof._safe_name("../../evil") is None
    assert spoof._safe_name("../alice") is None
    assert spoof._safe_name("") is None
    assert spoof._safe_name("alice") == "alice"
    assert spoof.enroll_speaker("../../evil", _sine()) is False
    assert not (speaker_dir.parent / "evil.json").exists()


# ── 特征向量形状 ─────────────────────────────────────


def test_feature_vector_shape() -> None:
    fx = spoof.extract_features(_sine())
    assert len(fx["vector"]) == spoof.PROFILE_POINTS
    assert 0.0 <= fx["silence_ratio"] <= 1.0
    assert fx["duration_s"] > 0.0


# ── 回放 / 环境噪声规则（不依赖声纹库） ───────────────


def test_silent_audio_is_spoof(speaker_dir) -> None:
    audio = np.zeros(int(16000 * 2.0), dtype=np.float32) + 1e-9
    r = spoof.anti_spoof(audio)
    assert r["ok"] is False
    assert "静音占比" in r["reason"]


def test_short_audio_is_spoof(speaker_dir) -> None:
    r = spoof.anti_spoof(_sine(dur=0.2))  # 短于 MIN_DURATION=0.5s
    assert r["ok"] is False
    assert "过短" in r["reason"]


# ── 正常合成语音通过 ─────────────────────────────────


def test_normal_speech_passes_bypass_when_unregistered(speaker_dir, monkeypatch) -> None:
    monkeypatch.setattr(spoof, "MIN_DURATION", 0.5)
    r = spoof.anti_spoof(_sine(), name=None)
    assert r["ok"] is True
    assert "未注册，跳过" in r["reason"]


def test_normal_speech_matches_enrolled(speaker_dir, monkeypatch) -> None:
    monkeypatch.setattr(spoof, "MIN_DURATION", 0.5)
    ref = _sine()
    assert spoof.enroll_speaker("bob", ref) is True
    r = spoof.anti_spoof(ref, name="bob")
    assert r["ok"] is True
    assert r["score"] >= 0.9
    assert "验证通过" in r["reason"]


def test_different_voice_profile_fails(speaker_dir, monkeypatch) -> None:
    """能量轮廓/尺度明显不同的合成声 → 分低判伪造。"""
    monkeypatch.setattr(spoof, "MIN_DURATION", 0.5)
    ref = _sine(freq=180.0)
    other = _flat_tone(freq=600.0, amp=0.01)  # 平直轮廓 + 极低能量，差异显著
    assert spoof.enroll_speaker("carol", ref) is True
    r = spoof.anti_spoof(other, name="carol")
    assert r["ok"] is False
    assert "不匹配" in r["reason"]


# ── 未注册旁路 ──────────────────────────────────────


def test_anti_spoof_with_unknown_name_bypasses(speaker_dir, monkeypatch) -> None:
    monkeypatch.setattr(spoof, "MIN_DURATION", 0.5)
    r = spoof.anti_spoof(_sine(), name="nobody")
    assert r["ok"] is True
    assert "未注册" in r["reason"]


# ── list / remove ───────────────────────────────────


def test_list_and_remove_speakers(speaker_dir) -> None:
    spoof.enroll_speaker("x", _sine())
    spoof.enroll_speaker("y", _sine())
    assert spoof.list_speakers() == ["x", "y"]
    assert spoof.remove_speaker("x") is True
    assert spoof.list_speakers() == ["y"]
    assert spoof.remove_speaker("x") is False  # 幂等
    assert spoof.remove_speaker("missing") is False


# ── int16 / float32 缩放不炸 ─────────────────────────


def test_int16_and_float32_scaling_no_panic(speaker_dir, monkeypatch) -> None:
    monkeypatch.setattr(spoof, "MIN_DURATION", 0.5)
    float32_audio = _sine()
    int16_audio = (float32_audio * 32768.0).astype(np.int16)  # int16 尺度
    # 注册 float32 版，用 int16 版查询应同样通过（归一化后一致）
    assert spoof.enroll_speaker("scale", float32_audio) is True
    r = spoof.anti_spoof(int16_audio, name="scale")
    assert r["ok"] is True
    # 静音 int16 也应被识别为 spoof（先归一化再判），不崩
    silent_i16 = np.zeros(16000, dtype=np.int16)
    rs = spoof.anti_spoof(silent_i16)
    assert rs["ok"] is False


def test_anti_spoof_never_raises_on_garbage() -> None:
    r = spoof.anti_spoof(np.array([], dtype=np.float32))
    assert isinstance(r, dict) and "ok" in r
    r2 = spoof.anti_spoof(np.array(["a", "b"], dtype=object))
    assert isinstance(r2, dict) and "ok" in r2