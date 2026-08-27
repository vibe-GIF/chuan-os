"""P4 媒体生成（skills/handlers/media_gen.py）测试。

音乐合成确定性、不触网；wav 用标准库 wave 读回校验；视频/图片后端占位提示。
视频/图片配置化 HTTP 后端（ADR-058）：本地 mock 服务器验证请求协议 + 落盘 + 降级。
"""

from __future__ import annotations

import http.server
import json
import threading
import wave
from pathlib import Path

import pytest

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


# --------------------------------------------------------------------- #
# 配置化 HTTP 后端（ADR-058）：mock 服务器全链路
# --------------------------------------------------------------------- #
class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """记录最近一次 POST 请求（路径/鉴权/JSON body），回显二进制 payload。"""

    last: dict = {}
    payload = b"fake-bytes"
    ctype = "image/png"
    status = 200

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 方法名固定
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).last = {
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "body": json.loads(body.decode("utf-8")),
        }
        self.send_response(type(self).status)
        self.send_header("Content-Type", type(self).ctype)
        self.send_header("Content-Length", str(len(type(self).payload)))
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *args) -> None:  # noqa: N805 - 抑制访问日志
        pass


@pytest.fixture
def mock_backend() -> str:
    """起本地 mock HTTP 后端，返回 base URL（127.0.0.1 随机端口）。"""
    server = http.server.HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/generate"
    server.shutdown()
    thread.join(timeout=2)


def test_image_backend_http_saves_png(tmp_path, mock_backend, monkeypatch) -> None:
    _RecordingHandler.payload = b"fake-image-bytes"
    _RecordingHandler.ctype = "image/png"
    _RecordingHandler.status = 200
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"image": {"endpoint": mock_backend, "api_key_env": "SEEDREAM_API_KEY", "timeout": 5}},
    )
    monkeypatch.setenv("SEEDREAM_API_KEY", "test-key")

    out = mg.media_generate("image", "一只猫", output_dir=str(tmp_path))

    assert "已生成图片" in out
    files = list(tmp_path.glob("image_*.png"))
    assert len(files) == 1 and files[0].stat().st_size == len(_RecordingHandler.payload)
    # 请求协议：POST /v1/generate，Bearer 鉴权，JSON body 带 prompt
    req = _RecordingHandler.last
    assert req["path"] == "/v1/generate"
    assert req["auth"] == "Bearer test-key"
    assert req["body"] == {"prompt": "一只猫"}


def test_video_backend_http_saves_mp4(tmp_path, mock_backend, monkeypatch) -> None:
    _RecordingHandler.payload = b"fake-video-bytes"
    _RecordingHandler.ctype = "video/mp4"
    _RecordingHandler.status = 200
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"video": {"endpoint": mock_backend, "api_key_env": "SEEDANCE_API_KEY", "timeout": 5}},
    )
    monkeypatch.setenv("SEEDANCE_API_KEY", "test-key")

    out = mg.media_generate("video", "做个视频", output_dir=str(tmp_path))

    assert "已生成视频" in out
    files = list(tmp_path.glob("video_*.mp4"))
    assert len(files) == 1 and files[0].stat().st_size == len(_RecordingHandler.payload)


def test_backend_http_jpeg_ctype_saves_jpg(tmp_path, mock_backend, monkeypatch) -> None:
    """Content-Type: image/jpeg → 落盘 .jpg（_CT_EXT 映射生效）。"""
    _RecordingHandler.payload = b"jpeg-bytes"
    _RecordingHandler.ctype = "image/jpeg"
    _RecordingHandler.status = 200
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"image": {"endpoint": mock_backend, "api_key_env": "SEEDREAM_API_KEY", "timeout": 5}},
    )
    monkeypatch.setenv("SEEDREAM_API_KEY", "k")

    out = mg.media_generate("image", "画张图", output_dir=str(tmp_path))
    assert "已生成图片" in out
    assert list(tmp_path.glob("image_*.jpg"))


def test_backend_http_unknown_ctype_fallback(tmp_path, mock_backend, monkeypatch) -> None:
    """未知 Content-Type（application/octet-stream）→ 按 kind 缺省后缀落盘。"""
    _RecordingHandler.payload = b"raw-bytes"
    _RecordingHandler.ctype = "application/octet-stream"
    _RecordingHandler.status = 200
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"image": {"endpoint": mock_backend, "api_key_env": "SEEDREAM_API_KEY", "timeout": 5}},
    )
    monkeypatch.setenv("SEEDREAM_API_KEY", "k")

    out = mg.media_generate("image", "画张图", output_dir=str(tmp_path))
    assert "已生成图片" in out
    assert list(tmp_path.glob("image_*.png"))


def test_backend_http_500_degrades(tmp_path, mock_backend, monkeypatch) -> None:
    """非 2xx 响应 → 可读失败提示，不落盘、不抛错。"""
    _RecordingHandler.status = 500
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"video": {"endpoint": mock_backend, "api_key_env": "SEEDANCE_API_KEY", "timeout": 5}},
    )
    monkeypatch.setenv("SEEDANCE_API_KEY", "k")

    out = mg.media_generate("video", "做个视频", output_dir=str(tmp_path))
    assert "调用失败" in out
    assert not list(tmp_path.glob("video_*"))


def test_backend_http_empty_body_degrades(tmp_path, mock_backend, monkeypatch) -> None:
    _RecordingHandler.payload = b""
    _RecordingHandler.status = 200
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"image": {"endpoint": mock_backend, "api_key_env": "SEEDREAM_API_KEY", "timeout": 5}},
    )
    monkeypatch.setenv("SEEDREAM_API_KEY", "k")

    out = mg.media_generate("image", "画张图", output_dir=str(tmp_path))
    assert "空响应" in out
    assert not list(tmp_path.glob("image_*"))


def test_backend_http_needs_key_hint(tmp_path, mock_backend, monkeypatch) -> None:
    """endpoint 已配但密钥缺失（环境变量 + secrets 都无）→ 未接入提示，不触网。"""
    monkeypatch.setattr(
        mg, "_load_media_cfg",
        lambda: {"image": {"endpoint": mock_backend, "api_key_env": "SEEDREAM_API_KEY", "timeout": 5}},
    )
    monkeypatch.delenv("SEEDREAM_API_KEY", raising=False)
    monkeypatch.setattr(mg, "_SECRETS_PATH", tmp_path / "no_secrets.yaml")

    out = mg.media_generate("image", "画张图", output_dir=str(tmp_path))
    assert "未接入" in out and "seedream" in out
    assert not list(tmp_path.glob("image_*"))


def test_backend_http_empty_endpoint_hint(tmp_path, monkeypatch) -> None:
    """endpoint 为空（默认）→ 未接入提示（对齐 V1 占位语义）。"""
    monkeypatch.setattr(mg, "_load_media_cfg", lambda: {"video": {}})
    out = mg.media_generate("video", "做个视频", output_dir=str(tmp_path))
    assert "未接入" in out and "seedance" in out
