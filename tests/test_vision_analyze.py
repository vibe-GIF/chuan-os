"""N50 视觉理解 handler 单元测试（无网络，模型调用 mock）。"""

from __future__ import annotations

from pathlib import Path

from skills.handlers import vision_analyze as va

from chuan.adapters.skill_loader import SkillRegistry


# --------------------------------------------------------------------- #
# skill 注册
# --------------------------------------------------------------------- #
def test_vision_skill_registered_via_registry() -> None:
    registry = SkillRegistry()  # 默认加载项目 skills/
    skill = registry.get("vision_analyze")
    assert skill is not None
    assert skill.kind == "handler"
    tool = skill.to_tool()
    assert tool is not None
    assert tool.name == "vision_analyze"


def test_vision_skill_has_trigger_keywords() -> None:
    registry = SkillRegistry()
    skill = registry.get("vision_analyze")
    assert skill is not None
    assert "看图" in skill.trigger.get("keywords", [])
    assert skill.matches("帮我看看这张图里有什么")


# --------------------------------------------------------------------- #
# 入参 / 错误降级（不碰网络）
# --------------------------------------------------------------------- #
def test_vision_analyze_no_input() -> None:
    out = va.vision_analyze("")
    assert "未提供图片" in out


def test_vision_analyze_missing_file() -> None:
    out = va.vision_analyze("nonexistent_image_xyz.png")
    assert "图片不存在" in out


def test_vision_analyze_no_api_key(monkeypatch, tmp_path) -> None:
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "")
    out = va.vision_analyze(str(img))
    assert "未配置视觉大脑 api_key" in out


def test_vision_analyze_mock_call_local_image(monkeypatch, tmp_path) -> None:
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 64)
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    captured: dict = {}

    def fake_call(model, base_url, api_key, image_url, prompt):
        captured.update(model=model, api_key=api_key, image_url=image_url, prompt=prompt)
        return "图片描述：一只猫在窗台上"

    monkeypatch.setattr(va, "_call_vision", fake_call)
    out = va.vision_analyze(str(img))
    assert out == "图片描述：一只猫在窗台上"
    assert captured["api_key"] == "sk-test"
    assert captured["image_url"].startswith("data:image/png;base64,")
    assert captured["prompt"] == va.DEFAULT_PROMPT


def test_vision_analyze_mock_call_url(monkeypatch) -> None:
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")
    captured: dict = {}

    def fake_call(model, base_url, api_key, image_url, prompt):
        captured["image_url"] = image_url
        return "网页截图内容"

    monkeypatch.setattr(va, "_call_vision", fake_call)
    out = va.vision_analyze("https://example.com/shot.png")
    assert out == "网页截图内容"
    assert captured["image_url"] == "https://example.com/shot.png"


def test_vision_analyze_model_call_failure_degrades(monkeypatch, tmp_path) -> None:
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"2" * 64)
    monkeypatch.setattr(va, "_resolve_api_key", lambda cfg: "sk-test")

    def boom(*_a, **_k):
        raise RuntimeError("api down")

    monkeypatch.setattr(va, "_call_vision", boom)
    out = va.vision_analyze(str(img))
    assert "视觉理解失败" in out

