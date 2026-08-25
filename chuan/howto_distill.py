"""L3 从做到造 —— 任务收尾自动提炼「怎么做」知识原子（N27）。

N26 的沉淀靠 agent 显式调 ``howto_save``（免费模型不会主动干），缺自动
沉淀。本模块在**任务成功收尾**后自动提炼候选做法原子：

1. **确定性门槛**：失败/太短/无实质/已有强命中原子/队列已满/重复任务 → 跳过
2. **提炼** 名称/触发场景/怎么做 —— LLM 可选润色，失败一律回退确定性提取
   （沿用 wiki ingest「LLM + 确定性回退」模式，免费模型不稳的教训）
3. **入 staging 队列**（``data/memory/howto_staging/``，vault 之外不污染
   FTS/wiki）**待人工确认**
4. 人工 ``HowToStore.approve`` 后才经 ``Wiki.write`` 落入 howto（index/lint/
   双链全复用），``discard`` 则丢弃

设计约束（与项目一致）：确定性核心；LLM 不进关键路径；全程旁路，绝不
阻断主流程答复（``PersonaRole._wrap_result`` 挂接）。
"""

from __future__ import annotations

import json
from typing import Any

from chuan.howto import HowToStore

# 门槛参数：任务太短/结果无实质不沉淀；结果过长截断；队列上限防堆积
_MIN_TASK_CHARS = 8
_MIN_PROCESS_CHARS = 40
_MAX_PROCESS_CHARS = 2000
_MAX_STAGING = 30

# 确定性取名：剥求助前缀、按首个标点截断
_PREFIXES = ("请帮我", "麻烦你帮我", "帮我", "请你", "麻烦", "请")
_SEPS = ("，", ",", "。", "；", ";", "！", "？", "?")
_TRIM = " ，。,;!、?？"

_DISTILL_PROMPT = """把一次成功完成的任务提炼成可复用的「怎么做」知识原子。

规则：
- 只基于给出的任务与成功结果提炼，严禁编造、严禁补充之外的信息
- 输出一个 JSON 对象：{"name": "原子名", "trigger": "触发场景", "process": "怎么做", "tools": ["工具名"]}
- name 简短（<=20 字），是可复用做法的名字
- trigger 说明何时该复用（<=60 字），process 是可复用的步骤/经验（<=800 字）
- tools 列出涉及的 agent/工具名（没有则空数组）
- 只输出 JSON，不要 Markdown 代码块、不要任何其他文字

任务：{task}
成功结果：{content}"""


def _derive_name(task: str) -> str:
    """确定性原子名：剥求助前缀，取首个标点前的 24 字。"""
    text = task.strip()
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    cut = min(len(text), 24)
    for sep in _SEPS:
        idx = text.find(sep)
        if 0 < idx < cut:
            cut = idx
    return text[:cut].strip(_TRIM) or "做法"


def _derive_trigger(task: str) -> str:
    """确定性触发场景：剥前缀后的任务文本（何时该复用）。"""
    text = task.strip()
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text[:80].strip(_TRIM) or task.strip()[:80]


def _derive_process(content: str) -> str:
    """确定性怎么做：成功结果全文（即这套做法的执行产物），截断上限。"""
    return content.strip()[:_MAX_PROCESS_CHARS]


class HowToDistiller:
    """任务收尾自动提炼 → staging 待人工确认（确定性核心，LLM 可选润色）。"""

    def __init__(
        self,
        memory: Any,
        brain: Any | None = None,
        store: HowToStore | None = None,
    ) -> None:
        self.memory = memory
        self._brain = brain
        self._store = store or HowToStore(memory)

    # ------------------------------------------------------------------ #
    # 入口：任务成功收尾后调用
    # ------------------------------------------------------------------ #
    def maybe_distill(
        self,
        task: str,
        content: str,
        *,
        success: bool = True,
        source: str = "",
        role: str = "",
    ) -> dict[str, Any] | None:
        """按门槛自动提炼；命中则写入 staging 队列并返回候选 dict，否则 None。

        返回的候选含 name/trigger/process/tools/source/task/created，供人工
        ``/howto`` 命令 approve / discard 决定去留。
        """
        if not self._should_distill(task, content, success):
            return None
        name, trigger, process, tools = self._refine(task, content)
        return self._store.stage(
            name, trigger, process, tools=tools,
            source=source or "", task=task, role=role,
        )

    # ------------------------------------------------------------------ #
    # 门槛（确定性，先廉后贵：长度判断优先于 FTS 召回）
    # ------------------------------------------------------------------ #
    def _should_distill(self, task: str, content: str, success: bool) -> bool:
        if not success:
            return False
        task, content = (task or "").strip(), (content or "").strip()
        if len(task) < _MIN_TASK_CHARS or len(content) < _MIN_PROCESS_CHARS:
            return False
        try:
            if self._store.suggest(task) is not None:
                return False  # 已有强命中原子，不重复沉淀
            pending = self._store.staging_list()
        except Exception:  # noqa: BLE001 - 门槛失败一律不沉淀（保守）
            return False
        if len(pending) >= _MAX_STAGING:
            return False
        if any(c.get("task") == task for c in pending):
            return False  # 同一任务已在队列，不重复入队
        return True

    # ------------------------------------------------------------------ #
    # 提炼：LLM 可选润色 + 确定性回退
    # ------------------------------------------------------------------ #
    def _refine(self, task: str, content: str) -> tuple[str, str, str, list[str]]:
        name, trigger, process, tools = (
            _derive_name(task), _derive_trigger(task),
            _derive_process(content), [],
        )
        if self._brain is None:
            return name, trigger, process, tools
        try:
            resp = self._brain.complete(
                _DISTILL_PROMPT
                .replace("{task}", task[:400])
                .replace("{content}", content[:1500]),
                system="你是知识原子提炼助手，只输出 JSON，不要任何其他文字。",
                temperature=0.2,
            )
            start, end = resp.find("{"), resp.rfind("}")
            if start == -1 or end <= start:
                return name, trigger, process, tools
            data = json.loads(resp[start:end + 1])
            if not isinstance(data, dict):
                return name, trigger, process, tools
            name = str(data.get("name") or name).strip()[:40] or name
            trigger = str(data.get("trigger") or trigger).strip()[:200] or trigger
            process = (
                str(data.get("process") or process).strip()[:_MAX_PROCESS_CHARS]
                or process
            )
            tools = [
                str(t).strip() for t in (data.get("tools") or []) if str(t).strip()
            ]
        except Exception:  # noqa: BLE001 - 免费模型输出退化回退确定性
            pass
        return name, trigger, process, tools
