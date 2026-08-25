"""N30 自动技能创建：门槛 / 提炼 / staging / approve 写 YAML+注册 / 注入复用 / 监督接口。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from chuan.adapters.skill_loader import Skill, SkillRegistry
from chuan.memory import Memory
from chuan.role import PersonaRole
from chuan.runtime_supervisor import RuntimeSupervisor
from chuan.skill_creator import SkillCreator, _derive_keywords


def _memory(tmp_path: Path) -> Memory:
    return Memory(vault_path=tmp_path / "vault")


def _creator(tmp_path: Path, skills_dir: Path) -> tuple[SkillCreator, SkillRegistry]:
    registry = SkillRegistry(skills_dir)
    creator = SkillCreator(_memory(tmp_path), skills_dir=skills_dir, registry=registry)
    return creator, registry


# --------------------------------------------------------------------- #
# 关键词提炼（确定性）
# --------------------------------------------------------------------- #
def test_derive_keywords_extracts_cjk() -> None:
    kws = _derive_keywords("帮我生成部署周报")
    assert "生成部署周报" in kws  # 剥前缀后整串（6 字，非停词）


def test_derive_keywords_caps_and_dedupes() -> None:
    kws = _derive_keywords("帮我部署周报并生成部署摘要")
    assert len(kws) <= 8
    assert len(kws) == len(set(kws))


# --------------------------------------------------------------------- #
# 门槛
# --------------------------------------------------------------------- #
def test_should_create_thresholds(tmp_path: Path) -> None:
    creator, _ = _creator(tmp_path, tmp_path / "skills")
    # 失败不沉淀
    assert creator.maybe_create("帮我部署周报", "很长很长的内容" * 10, success=False) is None
    # 任务太短
    assert creator.maybe_create("部署", "很长很长的内容" * 10) is None
    # 结果无实质（<40 字）
    assert creator.maybe_create("帮我部署周报", "搞定") is None


def test_should_create_skips_duplicate_task_and_existing_skill(
    tmp_path: Path,
) -> None:
    creator, registry = _creator(tmp_path, tmp_path / "skills")
    task, content = "帮我生成部署周报", "第一周完成了 3 项变更并汇总成周报。" * 4
    assert creator.maybe_create(task, content) is not None
    # 同一任务已在队列 → 不重复入队
    assert creator.maybe_create(task, content) is None
    # 已有同名技能（派生态 "生成部署周报"）→ 不沉淀
    registry.add("生成部署周报", {"name": "生成部署周报", "type": "prompt",
                                "trigger": {}, "prompt": "x"})
    assert creator.maybe_create(task, content) is None


# --------------------------------------------------------------------- #
# 提炼 + staging
# --------------------------------------------------------------------- #
def test_maybe_create_stages_candidate(tmp_path: Path) -> None:
    creator, _ = _creator(tmp_path, tmp_path / "skills")
    content = "1. 拉取变更\n2. 汇总 3 项\n3. 生成周报发给团队。" * 3
    cand = creator.maybe_create("帮我生成部署周报", content, source="role:ops")
    assert cand is not None
    assert cand["name"]
    assert cand["keywords"]
    assert cand["prompt"].startswith("1. 拉取变更")
    assert cand["source"] == "role:ops"
    assert len(creator.staging_list()) == 1
    assert creator.staging_get(cand["name"]) is not None


# --------------------------------------------------------------------- #
# approve 写 YAML + 注册 / discard
# --------------------------------------------------------------------- #
def test_approve_writes_yaml_and_registers(tmp_path: Path) -> None:
    creator, registry = _creator(tmp_path, tmp_path / "skills")
    content = "1. 拉取变更\n2. 汇总\n3. 生成周报。" * 3
    cand = creator.maybe_create("帮我生成部署周报", content)
    assert cand is not None
    path = creator.approve(cand["name"])
    assert path is not None and path.exists()

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["type"] == "prompt"
    assert data["name"] == cand["name"]
    assert data["trigger"]["keywords"] == cand["keywords"]
    assert data["prompt"].startswith("1. 拉取变更")

    skill = registry.get(cand["name"])
    assert skill is not None and skill.kind == "prompt"
    assert skill.matches("帮我生成部署周报")
    assert creator.staging_list() == []  # 确认后清队列


def test_approve_with_rename(tmp_path: Path) -> None:
    creator, registry = _creator(tmp_path, tmp_path / "skills")
    content = "1. 拉取变更\n2. 汇总\n3. 生成周报。" * 3
    cand = creator.maybe_create("帮我生成部署周报", content)
    path = creator.approve(cand["name"], rename="deploy_report")
    assert path is not None and path.name == "deploy_report.yaml"
    assert registry.get("deploy_report") is not None
    assert creator.staging_list() == []


def test_discard_removes_candidate(tmp_path: Path) -> None:
    creator, registry = _creator(tmp_path, tmp_path / "skills")
    cand = creator.maybe_create("帮我生成部署周报", "1. 拉取\n2. 汇总\n3. 生成。" * 3)
    assert cand is not None
    assert creator.discard(cand["name"]) is True
    assert creator.staging_list() == []
    assert not (tmp_path / "skills" / f"{cand['name']}.yaml").exists()
    assert registry.get(cand["name"]) is None


# --------------------------------------------------------------------- #
# Skill 触发匹配 + 渲染 + find_prompt_skill
# --------------------------------------------------------------------- #
def test_skill_matches_and_render() -> None:
    skill = Skill("deploy", {
        "type": "prompt", "trigger": {"keywords": ["部署周报", "deploy"]},
        "prompt": "按部署周报流程执行。",
    })
    assert skill.matches("帮我生成部署周报")
    assert skill.matches("run the deploy report")
    assert not skill.matches("帮我写首诗")
    assert skill.render_prompt() == "按部署周报流程执行。"
    assert skill.to_tool() is None  # prompt 型不产生工具


def test_find_prompt_skill_ignores_handler_and_misses(tmp_path: Path) -> None:
    reg = SkillRegistry(tmp_path / "skills")
    # handler 型：Skill.kind 由是否有 handler 键推断（type 字段不影响）
    reg.add("bash", {"type": "handler", "handler": {"module": "x", "function": "y"},
                     "trigger": {"keywords": ["bash"]}})
    reg.add("deploy", {"type": "prompt", "trigger": {"keywords": ["部署周报"]},
                       "prompt": "做法"})
    assert reg.find_prompt_skill("帮我部署周报") is not None
    assert reg.find_prompt_skill("run bash ls") is None  # handler 不参与注入
    assert reg.find_prompt_skill("写首诗") is None  # 无命中


# --------------------------------------------------------------------- #
# 角色注入（旁路安全路径）
# --------------------------------------------------------------------- #
def test_role_inject_skill_no_memory_returns_unchanged() -> None:
    role = PersonaRole.__new__(PersonaRole)
    role._memory = None
    assert role._maybe_inject_skill("帮我部署周报") == "帮我部署周报"
    assert role._inject_reference("帮我部署周报") == "帮我部署周报"


# --------------------------------------------------------------------- #
# 幕僚长管理接口（最小替身）
# --------------------------------------------------------------------- #
class _SupLike(RuntimeSupervisor):
    def __init__(self, memory: Memory, skills_dir: Path) -> None:
        self.memory = memory
        self.tool_registry = SimpleNamespace(skills=SkillRegistry(skills_dir))
        self.skill_creator = SkillCreator(
            memory, skills_dir=skills_dir, registry=self.tool_registry.skills
        )


def test_supervisor_skill_approve_and_status(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    sup = _SupLike(memory, tmp_path / "skills")
    cand = sup.skill_creator.maybe_create(
        "帮我生成部署周报", "1. 拉取变更\n2. 汇总 3 项\n3. 生成周报。" * 3)
    assert cand is not None
    assert len(sup.skill_staging()) == 1
    assert "描述" in sup.skill_show(cand["name"])

    msg = sup.skill_approve(cand["name"])
    assert "deploy" in msg or ".yaml" in msg
    assert sup.skill_staging() == []
    assert sup.skill_status()["registered"] == 1
    assert sup.skill_status()["pending"] == 0

    assert "未找到" in sup.skill_approve("不存在的候选")
    assert "已丢弃" not in sup.skill_discard("不存在的候选")
