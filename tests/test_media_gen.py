"""P4 媒体生成（skills/handlers/media_gen.py）测试。

音乐合成确定性、不触网；wav 用标准库 wave 读回校验；视频/图片后端占位提示。
"""

from __future__ import annotations

import wave
from pathlib import Path

from skills.handlers import media_gen as mg

from chuan.adapters.skill_loader import SkillRegistry


# --------------------------------------------------------------------- #
# skill 注册
# --------------------------------------------------------------------- #
def test_media_gen_skill_registered() -> None:
    registry = SkillRegistry()
    skill = registry.get("media_gen")
    assert skill is not None
    assert skill.kind == "handler"
    tool = skill.to_tool()
    assert tool is not None and tool.name == "media_gen"


def test_media_gen_has_trigger_keywords() -> None:
    registry = SkillRegistry()
    skill = registry.get("media_gen")
    assert "生成音乐" in skill.trigger.get("keywords", [])
    assert skill.matches("帮我生成一段背景音乐")


# --------------------------------------------------------------------- #
# 音乐合成：落盘 + 合法 wav
# --------------------------------------------------------------------- #
def test_music_writes_valid_wav(tmp_path) -> None:
    out = mg.media_generate("music", "欢快的音乐", output_dir=str(tmp_path))
    assert "已生成音乐" in out
    wavs = list(tmp_path.glob("music_*.wav"))
    assert len(wavs) == 1
    path = wavs[0]
    assert path.stat().st_size > 44  # 有真实 PCM 数据
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == mg.SAMPLE_RATE
        assert w.getnframes() > 0


def test_music_output_dir_auto_created(tmp_path) -> None:
    d = tmp_path / "nested" / "media"
    mg.media_generate("music", "", output_dir=str(d))
    assert d.exists() and list(d.glob("music_*.wav"))


# --------------------------------------------------------------------- #
# 情绪影响合成（确定性）
# --------------------------------------------------------------------- #
def test_sad_music_longer_than_happy() -> None:
    sad = mg._synth_music("悲伤的慢歌")
    happy = mg._synth_music("欢快的音乐")
    assert sad.size > happy.size  # 悲伤 bpm 低 → 更长


def test_synth_music_deterministic() -> None:
    a = mg._synth_music("")
    b = mg._synth_music("")
    assert a.shape == b.shape
    assert float(a.mean()) == float(b.mean())  # 同输入 → 同输出


# --------------------------------------------------------------------- #
# 后端占位 / 未知类型 / 静默降级
# --------------------------------------------------------------------- #
def test_video_backend_placeholder() -> None:
    out = mg.media_generate("video", "做个视频")
    assert "seedance" in out


def test_image_backend_placeholder() -> None:
    out = mg.media_generate("image", "画张图")
    assert "seedream" in out


def test_unknown_kind_degrades(tmp_path) -> None:
    out = mg.media_generate("midi", "", output_dir=str(tmp_path))
    assert "未知类型" in out
    assert "music / video / image" in out


def test_default_output_dir_runs() -> None:
    """不传 output_dir 时用默认 data/media：不抛错、返回可读文本。"""
    out = mg.media_generate("music", "测试")
    assert "已生成音乐" in out or "媒体生成" in out
