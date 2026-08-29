"""L3 从做到造 —— 自动技能创建（N30，技能即记忆）。

N26/N27 已把「怎么做」沉淀成知识原子（howto，wiki 第 6 类）并自动复用。
本模块再进一步：任务成功收尾时自动提炼一个**可注册的技能**（prompt 型 skill）
——比知识原子更强的复用单元：带触发关键词，命中即按「参考技能」注入做法。

模式复用 N27 howto 蒸馏（自动提炼 → 人工确认 → 入库）：
1. **确定性门槛**：失败 / 任务太短(<8 字) / 结果无实质(<40 字) / 已有同名技能
   / 队列已满(30) / 同一任务已在队列 → 跳过，绝不脑补
2. **提炼** name/description/keywords/prompt —— 纯确定性（无 LLM），沿用
   howto_distill 的剥前缀取名、结果作做法、任务作触发场景
3. **入 staging 队列**（``data/memory/skill_staging/``，vault 外不污染 FTS/wiki）
   待人工确认
4. 人工 ``/skill approve`` 后写入 ``skills/<name>.yaml``（prompt 型）并**运行时
   注册**进 ``SkillRegistry``（本会话立即生效，无需重启）；``discard`` 丢弃

与 howto 的分工：howto 是**知识**（FTS 召回注入参考做法）；skill 是**能力**
（触发关键词精确命中注入复用做法）——前者治「怎么做」，后者治「什么时候按
既有做法来」。二者可独立确认，各有各的队列上限。

设计约束（与项目一致）：确定性核心；LLM 不进关键路径；全程旁路，绝不阻断
主流程答复（``Department._wrap_result`` 挂接）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from chuan.adapters.skill_loader import SkillRegistry
from chuan.howto_distill import _derive_name, _derive_trigger

# 门槛参数（与 howto_distill 一致）：任务太短/结果无实质不沉淀；队列上限防堆积
_MIN_TASK_CHARS = 8
_MIN_PROCESS_CHARS = 40
_MAX_PROCESS_CHARS = 2000
_MAX_STAGING = 30
_MAX_KEYWORDS = 8

# 确定性关键词提取：CJK 词去噪
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z0-9_]{2,}")
_STOPWORDS = frozenset({
    "帮我", "请你", "麻烦", "麻烦你", "可以", "一下", "这个", "那个", "任务",
    "把", "要", "需要", "生成", "做", "写", "整理", "汇总", "总结", "汇报",
    "自动", "每周", "的", "了", "和", "与", "对", "用", "给", "我", "你",
    "它", "这些", "那些", "还有", "以及", "然后", "就", "都", "很",
})


def _derive_description(task: str) -> str:
    """确定性描述：剥前缀后的触发场景，说明「什么时候用这个技能」。"""
    return f"复用技能：{_derive_trigger(task)}"


def _derive_keywords(task: str) -> list[str]:
    """确定性触发关键词：从任务文本提取 CJK 词/二元组（去掉通用词），上限 8 个。

    长 CJK 串（>6 字）退化为相邻二元组，保证后续任务子串命中。
    """
    text = _derive_trigger(task)
    kws: list[str] = []
    for run in _CJK.findall(text):
        if 2 <= len(run) <= 6 and run not in _STOPWORDS and run not in kws:
            kws.append(run)
        elif len(run) > 6:
            for i in range(len(run) - 1):
                gram = run[i:i + 2]
                if gram not in _STOPWORDS and gram not in kws:
                    kws.append(gram)
    for word in _LATIN.findall(text.lower()):
        if word not in _STOPWORDS and word not in kws:
            kws.append(word)
    return kws[:_MAX_KEYWORDS]


def _derive_prompt(content: str) -> str:
    """确定性做法：成功结果全文（这套做法的执行产物），截断上限。"""
    return content.strip()[:_MAX_PROCESS_CHARS]


class SkillCreator:
    """任务收尾自动提炼 → staging 待人工确认 → approve 写 YAML + 运行时注册。"""

    def __init__(
        self,
        memory: Any,
        skills_dir: str | Path = "skills",
        registry: SkillRegistry | None = None,
    ) -> None:
        self.memory = memory
        self._registry = registry or SkillRegistry(skills_dir)
        self._skills_dir_path = self._resolve_skills_dir(skills_dir)

    def _resolve_skills_dir(self, skills_dir: str | Path) -> Path:
        path = Path(skills_dir)
        if not path.is_absolute():
            # 本文件位于 chuan/skill_creator.py，向上两级即项目根
            path = Path(__file__).resolve().parent.parent / path
        return path

    # ------------------------------------------------------------------ #
    # 入口：任务成功收尾后调用（旁路）
    # ------------------------------------------------------------------ #
    def maybe_create(
        self,
        task: str,
        content: str,
        *,
        success: bool = True,
        source: str = "",
        role: str = "",
    ) -> dict[str, Any] | None:
        """按门槛自动提炼；命中则写入 staging 队列并返回候选 dict，否则 None。"""
        if not self._should_create(task, content, success):
            return None
        cand = {
            "name": self._derive_name(task),
            "description": _derive_description(task),
            "keywords": _derive_keywords(task),
            "prompt": _derive_prompt(content),
            "source": source or "",
            "task": (task or "").strip(),
            "role": role,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        self.stage(cand)
        return cand

    # ------------------------------------------------------------------ #
    # 门槛（确定性，先廉后贵：长度判断优先于注册表/队列查询）
    # ------------------------------------------------------------------ #
    def _should_create(self, task: str, content: str, success: bool) -> bool:
        if not success:
            return False
        task, content = (task or "").strip(), (content or "").strip()
        if len(task) < _MIN_TASK_CHARS or len(content) < _MIN_PROCESS_CHARS:
            return False
        try:
            pending = self.staging_list()
        except Exception:  # noqa: BLE001 - 门槛失败一律不沉淀（保守）
            return False
        if len(pending) >= _MAX_STAGING:
            return False
        if any(c.get("task") == task for c in pending):
            return False  # 同一任务已在队列，不重复入队
        if self._registry.get(self._derive_name(task)) is not None:
            return False  # 已有同名技能，不重复沉淀
        return True

    @staticmethod
    def _derive_name(task: str) -> str:
        return _derive_name(task)

    # ------------------------------------------------------------------ #
    # staging 待人工确认队列（vault 之外，避免污染 FTS/wiki）
    # ------------------------------------------------------------------ #
    def _staging_dir(self) -> Path:
        return self.memory.vault_path.parent / "skill_staging"

    def _staging_file(self, name: str) -> Path:
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name.strip()).strip("-") or "skill"
        return self._staging_dir() / f"{slug}.json"

    def stage(self, cand: dict[str, Any]) -> dict[str, Any]:
        d = self._staging_dir()
        d.mkdir(parents=True, exist_ok=True)
        self._staging_file(cand["name"]).write_text(
            json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return cand

    def staging_list(self) -> list[dict[str, Any]]:
        """列出待人工确认的候选（按创建时间倒序）；损坏项跳过。"""
        d = self._staging_dir()
        if not d.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(d.glob("*.json"), reverse=True):
            try:
                cand = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(cand, dict) and cand.get("name"):
                out.append(cand)
        return out

    def staging_get(self, name: str) -> dict[str, Any] | None:
        path = self._staging_file(name)
        if not path.exists():
            return None
        try:
            cand = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cand if isinstance(cand, dict) else None

    def discard(self, name: str) -> bool:
        """人工否决：删除候选，不入库。"""
        path = self._staging_file(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------------ #
    # 人工确认：写 skills/<name>.yaml + 运行时注册进 SkillRegistry
    # ------------------------------------------------------------------ #
    def approve(self, name: str, rename: str | None = None) -> Path | None:
        """人工确认：候选写成 prompt 型技能 YAML 并注册进注册表（本会话即生效）。

        ``rename`` 可指定入库时的新技能名；返回 YAML 落盘路径，失败返回 None。
        """
        cand = self.staging_get(name)
        if cand is None:
            return None
        final_name = (rename or cand["name"] or name).strip()
        if not final_name or not cand.get("prompt", "").strip():
            return None

        definition = {
            "name": final_name,
            "description": cand.get("description") or f"复用技能：{final_name}",
            "type": "prompt",
            "trigger": {"keywords": list(cand.get("keywords") or [])},
            "prompt": cand.get("prompt", "").strip(),
            "params": {},
        }
        skill_path = self._skills_dir_path / f"{final_name}.yaml"
        self._skills_dir_path.mkdir(parents=True, exist_ok=True)
        header = f"# 自动沉淀技能：{final_name}（N30 技能即记忆，/skill approve 人工确认）\n"
        skill_path.write_text(
            header + yaml.safe_dump(definition, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        # 运行时注册：本会话立即生效，无需重启
        self._registry.add(final_name, definition)
        self._staging_file(name).unlink(missing_ok=True)
        return skill_path

    # ------------------------------------------------------------------ #
    # 展示
    # ------------------------------------------------------------------ #
    def show(self, name: str) -> str:
        """渲染单个候选的可读文本（供 /skill show）。"""
        cand = self.staging_get(name)
        if cand is None:
            return f"（未找到待确认技能候选：{name}）"
        kws = "、".join(cand.get("keywords") or []) or "（无）"
        return (
            f"【技能候选】{cand['name']}\n"
            f"描述：{cand.get('description') or '—'}\n"
            f"触发关键词：{kws}\n"
            f"来源：{cand.get('source') or '—'} · 任务：{cand.get('task') or '—'}\n"
            f"可复用做法：\n{cand.get('prompt', '')[:600]}"
        )
