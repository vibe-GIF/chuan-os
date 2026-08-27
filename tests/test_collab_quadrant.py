"""四象限协作框架 prompt 型技能（skills/collab_quadrant.yaml，N30）测试。

验证：skill 以 prompt 类型注册、触发词命中即返回、prompt 内容含协作纪律、
非 prompt 场景（handler 工具转换）返回 None、无关文本不误触。
"""

from __future__ import annotations

from chuan.adapters.skill_loader import SkillRegistry


def test_collab_quadrant_registered_as_prompt() -> None:
    registry = SkillRegistry()
    skill = registry.get("collab_quadrant")
    assert skill is not None
    assert skill.kind == "prompt"  # 无 handler/mcp_server → 自动判为 prompt 型
    assert "四象限" in skill.render_prompt()


def test_collab_quadrant_trigger_hits() -> None:
    registry = SkillRegistry()
    # 直接 matches
    assert registry.get("collab_quadrant").matches("用四象限框架帮我做方案")
    assert registry.get("collab_quadrant").matches("做个需求分析")
    # find_prompt_skill：注入入口（PersonaRole._maybe_inject_skill 用）
    assert registry.find_prompt_skill("按四象限协作处理这个项目") is not None
    assert registry.find_prompt_skill("帮我设计一个方案") is not None


def test_collab_quadrant_no_false_positive() -> None:
    """无关任务文本不误触（避免给简单任务注入过重纪律）。"""
    registry = SkillRegistry()
    assert registry.find_prompt_skill("帮我查一下天气") is None
    assert registry.find_prompt_skill("今天几度") is None


def test_collab_quadrant_prompt_content() -> None:
    """prompt 含协作纪律要点（共同已知 / 最多 3 问 / 主动挑错 / 假设标注）。"""
    p = SkillRegistry().get("collab_quadrant").render_prompt()
    for key in ("共同已知", "3 个关键问题", "不要一味顺从", "可验证假设", "内部思考逻辑"):
        assert key in p


def test_collab_quadrant_not_a_callable_tool() -> None:
    """prompt 型技能非可调用工具：to_tool 返回 None（handler 才转 LangChain Tool）。"""
    assert SkillRegistry().get("collab_quadrant").to_tool() is None
