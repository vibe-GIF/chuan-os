"""N20 自改进循环（GEPA）—— Generate-Execute-Preserve-Assess。

角色每次 ``dispatch`` 完成任务后，自动评估结果、把「经验/教训」沉淀到
角色私有记忆 ``MEMORY.md``（仅 ADR-013 目录格式角色），供下次任务读回。

GEPA 四步在现有链路中的映射：
- Generate / Execute → ``PersonaRole.dispatch`` / ``_execute``（已完成）
- Assess  → :func:`assess` 确定性评估，产出「可沉淀的一条经验」
- Preserve → :func:`preserve` 追加到 ``<角色目录>/MEMORY.md``

自改进是旁路语义：评估/落盘失败绝不阻断主流程（调用方负责 try/except）。
免费模型 JSON 不稳的教训下，当前评估走确定性路径（不调 LLM），避免脑补。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

_MEMORY_FILE = "MEMORY.md"
_MAX_SUMMARY = 120  # 经验摘要最长字符数（截断，防超长污染记忆）


def assess(task: str, content: str, success: bool) -> str | None:
    """确定性评估：把一次执行结果浓缩成一条可沉淀的经验；无价值返回 None。

    只做「成功/失败 + 结论摘要」，不做语义判断，绝不脑补：
    - 内容为空 → 无可沉淀，返回 None
    - 内容冗长 → 截断到 ``_MAX_SUMMARY`` 字
    """
    text = (content or "").strip()
    if not text:
        return None
    summary = " ".join(text.split())[:_MAX_SUMMARY]
    if not summary:
        return None
    stamp = datetime.now().isoformat(timespec="minutes")
    mark = "完成" if success else "未完成"
    return f"- {stamp} [{mark}] {task}：{summary}"


def preserve(persona: Any, lesson: str) -> str | None:
    """把一条经验追加到目录角色（ADR-013）的 ``MEMORY.md``；非目录角色返回 None。

    返回写入后的文件路径；落盘失败抛 OSError（由调用方决定是否吞掉）。
    """
    directory = getattr(persona, "directory", None)
    if not directory:
        return None
    memory_file = Path(directory) / _MEMORY_FILE
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    with memory_file.open("a", encoding="utf-8") as f:
        f.write(lesson.rstrip() + "\n")
    return str(memory_file)


def run_gepa(persona: Any, task: str, content: str, success: bool) -> bool:
    """GEPA 全链路：评估 → 保存。仅目录格式角色生效，无内容/失败返回 False。

    返回是否真正沉淀了一条经验（写入 MEMORY.md）。
    """
    directory = getattr(persona, "directory", None)
    if not directory:
        return False
    lesson = assess(task, content, success)
    if lesson is None:
        return False
    return preserve(persona, lesson) is not None