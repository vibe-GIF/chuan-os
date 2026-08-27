"""陌生人识别 + 自动锁屏（P4/N59，chuan/security/guard.py）测试。

安全要点：guard.lock_workstation 在 Windows 真调用会锁屏，测试一律
monkeypatch 成假实现 / 注入 lock_cb，绝不触发真实锁屏。
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from chuan.security import guard
from chuan.voice import spoof


def _sine(dur: float = 2.0, freq: float = 180.0, amp: float = 0.3, sr: int = 16000) -> np.ndarray:
    """合成人声样（正弦 + 汉宁包络），与 test_voice_spoof 同款。"""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    envelope = np.hanning(int(sr * dur))
    sig = amp * envelope * np.sin(2 * np.pi * freq * t)
    return sig.astype(np.float32)


def _flat_tone(dur: float = 2.0, freq: float = 600.0, amp: float = 0.01, sr: int = 16000) -> np.ndarray:
    """另一路「声」：无包络平直正弦（能量轮廓/尺度与 _sine 明显不同）。"""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


@pytest.fixture
def speaker_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> object:
    """把注册目录指向临时目录，避免污染真实 data/speakers。"""
    d = tmp_path / "speakers"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(spoof, "_SPEAKERS_DIR", d)
    return d


# ── identify_speaker ────────────────────────────────


def test_identify_unregistered_returns_unknown(speaker_dir) -> None:
    name, score = guard.identify_speaker(_sine())
    assert name is None
    assert score == 0.0  # 无注册声纹 → unknown


def test_identify_matches_enrolled(speaker_dir) -> None:
    ref = _sine()
    assert spoof.enroll_speaker("alice", ref) is True
    name, score = guard.identify_speaker(ref)
    assert name == "alice"
    assert score >= 0.7


def test_identify_different_voice_is_stranger(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine(freq=180.0)) is True
    # 陌生人声相似度实测 ~0.565，低于默认阈值 0.6 → 判陌生人
    name, score = guard.identify_speaker(_flat_tone(freq=600.0, amp=0.01))
    assert name is None  # 陌生人
    assert score > 0.0  # 有分但低于阈值
    assert score < 0.6


def test_identify_unreliable_audio_is_unknown(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine()) is True
    # 过短音频 → unknown（不判陌生人，避免误锁屏）
    name, score = guard.identify_speaker(_sine(dur=0.2))
    assert name is None
    assert score == 0.0
    # 纯静音 → unknown
    name2, score2 = guard.identify_speaker(np.zeros(32000, dtype=np.float32) + 1e-9)
    assert name2 is None and score2 == 0.0


def test_identify_picks_best_speaker(speaker_dir) -> None:
    ref = _sine(freq=180.0)
    assert spoof.enroll_speaker("alice", ref) is True
    assert spoof.enroll_speaker("bob", _sine(freq=400.0)) is True
    name, _ = guard.identify_speaker(ref)
    assert name == "alice"


def test_identify_never_raises_on_garbage() -> None:
    name, score = guard.identify_speaker(np.array([], dtype=np.float32))
    assert name is None and score == 0.0
    name2, score2 = guard.identify_speaker(np.array(["x"], dtype=object))
    assert name2 is None and score2 == 0.0


# ── lock_workstation ────────────────────────────────


def test_lock_workstation_non_windows_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="posix"))
    assert guard.lock_workstation() is False


def test_lock_workstation_windows_failure_returns_false(monkeypatch) -> None:
    class _FakeWin:
        class _u:
            @staticmethod
            def LockWorkStation() -> bool:
                return False

        windll = SimpleNamespace(user32=_u())

    monkeypatch.setattr(guard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guard, "ctypes", _FakeWin())
    assert guard.lock_workstation() is False


def test_lock_workstation_windows_success(monkeypatch) -> None:
    class _FakeWin:
        class _u:
            @staticmethod
            def LockWorkStation() -> bool:
                return True

        windll = SimpleNamespace(user32=_u())

    monkeypatch.setattr(guard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guard, "ctypes", _FakeWin())
    assert guard.lock_workstation() is True


# ── SecurityGuard ───────────────────────────────────


def test_guard_disabled_by_default() -> None:
    g = guard.SecurityGuard()
    assert g.enabled is False
    verdict = g.check(_sine())
    assert verdict["verdict"] == "disabled"
    assert verdict["locked"] is False


def test_guard_owner_clears_streak(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine()) is True
    g = guard.SecurityGuard(enabled=True, lock_cb=lambda: None)
    v1 = g.check(_sine())
    assert v1["verdict"] == "owner"
    assert v1["streak"] == 0
    assert v1["locked"] is False


def test_guard_stranger_streak_triggers_lock(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine(freq=180.0)) is True
    calls: list[str] = []
    g = guard.SecurityGuard(enabled=True, streak=3, lock_cb=lambda: calls.append("lock"))
    other = _flat_tone(freq=600.0, amp=0.01)
    for _ in range(2):
        v = g.check(other)
        assert v["verdict"] == "stranger"
        assert v["locked"] is False
    v3 = g.check(other)
    assert v3["locked"] is True
    assert calls == ["lock"]
    # 锁屏后计数复位
    v4 = g.check(other)
    assert v4["streak"] == 1
    assert v4["locked"] is False


def test_guard_owner_resets_fail_streak(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine(freq=180.0)) is True
    calls: list[str] = []
    g = guard.SecurityGuard(enabled=True, streak=2, lock_cb=lambda: calls.append("lock"))
    other = _flat_tone(freq=600.0, amp=0.01)
    g.check(other)  # 1 次陌生人
    owner = g.check(_sine(freq=180.0))  # 主人声纹 → 复位
    assert owner["verdict"] == "owner"
    g.check(other)  # 重新计数
    v = g.check(other)  # 达到 2 次
    assert v["locked"] is True
    assert calls == ["lock"]


def test_guard_lock_cb_failure_does_not_raise(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine(freq=180.0)) is True

    def _boom() -> None:
        raise RuntimeError("lock failed")

    g = guard.SecurityGuard(enabled=True, streak=1, lock_cb=_boom)
    v = g.check(_flat_tone(freq=600.0, amp=0.01))
    assert v["locked"] is True  # 锁屏回调异常被吞，判定不受影响


def test_guard_unknown_does_not_count_streak(speaker_dir) -> None:
    assert spoof.enroll_speaker("alice", _sine()) is True
    g = guard.SecurityGuard(enabled=True, streak=1, lock_cb=lambda: None)
    # 过短音频 → unknown，不计入连续陌生人计数
    v = g.check(_sine(dur=0.2))
    assert v["verdict"] == "unknown"
    assert v["streak"] == 0


def test_guard_never_raises_on_garbage() -> None:
    g = guard.SecurityGuard(enabled=True, lock_cb=lambda: None)
    v = g.check(np.array([], dtype=np.float32))
    assert "verdict" in v
    assert v["locked"] is False
