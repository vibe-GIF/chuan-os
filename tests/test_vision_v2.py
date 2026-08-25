"""N52 视觉理解 V2 扩展单元测试（PDF/表格/视频 → 图走视觉管线，无网络，模型调用 mock）。

覆盖：PDF 缺依赖降级提示、csv 渲染出图、xlsx 留提示、视频抽帧 mock、无输入降级、
缺 ffmpeg 降级、合法图片仍走原 data URI 路径，以及 skill 新增触发词。
"""

from __future__ import annotations

import builtins
from pathlib import Path

from skills.handlers import vision_analyze as va

from chuan.adapters.skill_loader import SkillRegistry


# --------------------------------------------------------------------- #
# skill 注册与触发词
# --------------------------------------------------------------------- #
def _registry_skill() -> object:
    registry = SkillRegistry()  # 默认加载项目 skills/
    skill = registry.get("vision_analyze")
    assert skill is not None, "vision_analyze skill 应已注册"
    return skill


def test_vision_v2_skill_has_new_trigger_keywords() -> None:
    skill = _registry_skill()
    kws = skill.trigger.get("keywords", [])
    for kw in ["读表格", "看 PDF", "看视频", "视频截图"]:
        assert kw in kws, f"触发词缺 {kw}"
    assert skill.matches("帮我读表格")


# --------------------------------------------------------------------- #
# 无输入 / 合法图片仍走原路径
# --------------------------------------------------------------------- #
def test_vision_v2_no_input() -> None:
    out = va.vision_analyze("")
    assert "未提供图片" in out


def test_vision_v2_legacy_image_still_goes_data_uri(monkeypatch, tmp_path) -> None:
    """V2 不破坏原有图片分支：未知/图片扩展名仍走 _image_data_uri。"""
    img = tmp_path / "legacy.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    uri, msg = va._resource_to_data_uri(img)
    assert uri and uri.startswith("data:image/png;base64,")
    assert msg is None


# --------------------------------------------------------------------- #
# PDF 缺依赖降级提示
# --------------------------------------------------------------------- #
def test_vision_v2_pdf_missing_dep_degrades(monkeypatch, tmp_path) -> None:
    """pdf2image 未装（V2 环境）→ 返回「需安装」可读提示，不抛错。"""
    orig = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "pdf2image":
            raise ImportError("No module named 'pdf2image'")
        return orig(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    called = []
    monkeypatch.setattr(va, "_call_vision", lambda *_a, **_k: called.append(1) or "")

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfake")
    out = va.vision_analyze(str(pdf))
    assert "需安装" in out
    assert "pdf2image" in out
    assert called == []  # 未走到模型调用


# --------------------------------------------------------------------- #
# csv 渲染出图（真实渲染，Pillow 已装）
# --------------------------------------------------------------------- #
def test_vision_v2_csv_renders_to_image_data_uri(tmp_path) -> None:
    csv = tmp_path / "sheet.csv"
    csv.write_text("姓名,城市,得分\n张三,上海,88\n李四,北京,95\n", encoding="utf-8")
    uri, msg = va._table_to_img(csv)
    assert uri is not None
    assert uri.startswith("data:image/jpeg;base64,")
    assert msg is None


def test_vision_v2_csv_through_model(monkeypatch, tmp_path) -> None:
    """csv 渲染出图后走视觉模型（mock _call_vision 校验收到 data URI）。"""
    csv = tmp_path / "scores.csv"
    csv.write_text("科目,分数\n数学,90\n语文,85\n", encoding="utf-8")
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    captured: dict = {}
    monkeypatch.setattr(
        va, "_call_vision",
        lambda model, base_url, api_key, image_url, prompt: (
            captured.__setitem__("image_url", image_url) or "表格内容：数学90语文85"
        ),
    )
    out = va.vision_analyze(str(csv))
    assert out == "表格内容：数学90语文85"
    assert captured["image_url"].startswith("data:image/jpeg;base64,")


def test_vision_v2_xlsx_kept_prompt(tmp_path) -> None:
    xlsx = tmp_path / "t.xlsx"
    xlsx.write_bytes(b"PK\x03\x04 fake")
    uri, msg = va._table_to_img(xlsx)
    assert uri is None
    assert "xlsx 暂未支持" in (msg or "")


def test_vision_v2_csv_missing_pillow_degrades(monkeypatch, tmp_path) -> None:
    """pillow 缺失 → csv 渲染降级为可读提示。"""
    csv_path = tmp_path / "s.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")

    orig = builtins.__import__

    def fake_import(name, *a, **k):
        if name.split(".")[0] == "PIL":
            raise ImportError("No module named 'PIL'")
        return orig(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    uri, msg = va._table_to_img(csv_path)
    assert uri is None
    assert "需安装" in (msg or "")


# --------------------------------------------------------------------- #
# 视频抽帧 mock
# --------------------------------------------------------------------- #
def test_vision_v2_video_frame_mocked(monkeypatch, tmp_path) -> None:
    """mock 抽帧结果，验证视频走模型且首帧 data URI 被传给视觉管线。"""
    fake = tmp_path / "clip.mp4"
    fake.write_bytes(b"\x00\x00\x00 fake mp4")
    monkeypatch.setattr(va, "_ffmpeg_extract_first_frame", lambda p: "data:image/jpeg;base64,M0NU")
    monkeypatch.setattr(va, "_ffmpeg_bin", lambda: "C:/ffmpeg.exe")
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    captured: dict = {}
    monkeypatch.setattr(
        va, "_call_vision",
        lambda model, base_url, api_key, image_url, prompt: (
            captured.__setitem__("image_url", image_url) or "视频首帧内容：办公室场景"
        ),
    )
    out = va.vision_analyze(str(fake))
    assert out == "视频首帧内容：办公室场景"
    assert captured["image_url"] == "data:image/jpeg;base64,M0NU"


def test_vision_v2_video_missing_ffmpeg_degrades(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "clip.mkv"
    fake.write_bytes(b"fake")
    monkeypatch.setattr(va, "_ffmpeg_bin", lambda: None)
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    out = va.vision_analyze(str(fake))
    assert "需安装" in out


def test_vision_v2_video_extract_failure_degrades(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "clip.mov"
    fake.write_bytes(b"broken")
    monkeypatch.setattr(va, "_ffmpeg_bin", lambda: "C:/ffmpeg.exe")
    monkeypatch.setattr(va, "_ffmpeg_extract_first_frame", lambda p: None)
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    out = va.vision_analyze(str(fake))
    assert "抽帧失败" in out