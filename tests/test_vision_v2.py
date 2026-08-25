"""N52 视觉理解 V2 handler 测试（PDF/表格/视频转图分派，无真实网络，模型调用 mock）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from skills.handlers import vision_analyze as va


def _write_fake_image(path: Path) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return path


def _has_pdf2image() -> bool:
    try:
        import pdf2image  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _has_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture()
def vision_mock(monkeypatch):
    """注入 sk-test key + 捕获 _call_vision 的调用。"""
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    captured: dict = {}

    def fake_call(model, base_url, api_key, image_url, prompt):
        captured.update(model=model, api_key=api_key, image_url=image_url)
        return "分析结果：视频/文档内容"

    monkeypatch.setattr(va, "_call_vision", fake_call)
    return captured


# --------------------------------------------------------------------- #
# 视频 / 录屏
# --------------------------------------------------------------------- #
def test_video_frame_real_ffmpeg(vision_mock, tmp_path) -> None:
    """ffmpeg 可用时真造小视频 → 抽首帧 → 走视觉。"""
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("环境无 ffmpeg")
    vid = tmp_path / "clip.mp4"
    proc = subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=5", str(vid)],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not vid.is_file():
        pytest.skip("ffmpeg 造视频失败")

    out = va.vision_analyze(str(vid))
    assert out == "分析结果：视频/文档内容"
    assert vision_mock["image_url"].startswith("data:image/png;base64,")


def test_video_missing_ffmpeg_degrades(monkeypatch, tmp_path) -> None:
    """缺 ffmpeg → 可读提示，不抛错。"""
    monkeypatch.setattr(va, "_ffmpeg_bin", lambda: None)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    out = va.vision_analyze(str(vid))
    assert "ffmpeg" in out


def test_video_ffmpeg_failure_degrades(vision_mock, monkeypatch, tmp_path) -> None:
    """ffmpeg 抽帧失败（坏文件）→ 可读提示。"""
    vid = tmp_path / "bad.mp4"
    vid.write_bytes(b"\x00\x00\x00not-a-real-video")
    out = va.vision_analyze(str(vid))
    assert "ffmpeg" in out or "视觉理解" in out


# --------------------------------------------------------------------- #
# PDF（缺依赖降级；装了则走真实转图）
# --------------------------------------------------------------------- #
def test_pdf_missing_dependency_degrades(vision_mock, tmp_path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    out = va.vision_analyze(str(pdf))
    if _has_pdf2image():
        # 装了：转图成功 → 走视觉（mock 返回）
        assert out == "分析结果：视频/文档内容"
        assert vision_mock["image_url"].startswith("data:image/png;base64,")
    else:
        assert "pdf2image" in out


# --------------------------------------------------------------------- #
# 表格（缺依赖降级；装了则走真实渲染）
# --------------------------------------------------------------------- #
def test_csv_missing_dependency_degrades(vision_mock, tmp_path) -> None:
    csv_f = tmp_path / "data.csv"
    csv_f.write_text("姓名,城市\n小明,北京\n", encoding="utf-8")
    out = va.vision_analyze(str(csv_f))
    if _has_pillow():
        # 装了：渲染表格图 → 走视觉（mock 返回）
        assert out == "分析结果：视频/文档内容"
        assert vision_mock["image_url"].startswith("data:image/png;base64,")
    else:
        assert "Pillow" in out


def test_csv_empty_table_degrades(vision_mock, tmp_path) -> None:
    csv_f = tmp_path / "empty.csv"
    csv_f.write_text("", encoding="utf-8")
    out = va.vision_analyze(str(csv_f))
    assert "视觉理解" in out


# --------------------------------------------------------------------- #
# 回归：图片 / URL 行为不变
# --------------------------------------------------------------------- #
def test_image_still_works(vision_mock, tmp_path) -> None:
    img = _write_fake_image(tmp_path / "shot.png")
    out = va.vision_analyze(str(img))
    assert out == "分析结果：视频/文档内容"
    assert vision_mock["image_url"].startswith("data:image/png;base64,")


def test_url_still_passthrough(vision_mock) -> None:
    out = va.vision_analyze("https://example.com/shot.png")
    assert out == "分析结果：视频/文档内容"
    assert vision_mock["image_url"] == "https://example.com/shot.png"


def test_unknown_extension_treated_as_image(vision_mock, tmp_path) -> None:
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"9" * 64)
    out = va.vision_analyze(str(f))
    assert out == "分析结果：视频/文档内容"
