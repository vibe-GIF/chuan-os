"""L4 记忆闭环 —— 旧会话巩固（consolidation worker）。

把 data/sessions.db 里的会话（LangGraph checkpoint）蒸馏成持久 Markdown
笔记写入 ``notes/session-*.md``，完成"短期会话 → 长期记忆"的最后一跳。

摘要策略：
- 优先 LLM（可失败，免费模型输出退化时退回）
- 失败/无模型时确定性抽取（结论 + 讨论过的问题）

追踪表 data/consolidation.db 记录每个线程已蒸馏到的 checkpoint：
- 从未蒸馏 → 蒸馏全量
- 蒸馏过但 checkpoint 已前进（有新消息）→ 重蒸馏覆盖笔记（保留 created）
- checkpoint 未变 → 跳过

说明：checkpoint_id 不是时序 ID，没有可靠墙钟，因此"旧"由
"消息条数达标 + 有未蒸馏内容" 近似；worker 在启动时后台跑一次，自然收敛旧账。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

_SKIP_PREFIX = "ask:"  # 协作线程（成员直通）不入长期记忆
_SKIP_FRAGMENT = ":plan:"  # 子任务线程（结果已并入主会话答复）
_TRACKING_DB = "data/consolidation.db"
_DEFAULT_MIN_MESSAGES = 4
_MAX_LLM_TRANSCRIPT_CHARS = 8000
_LLM_TRANSCRIPT_TAIL = 40  # LLM 摘要只喂最近的 N 条消息，防超长

_SUMMARY_PROMPT = """把下面的对话浓缩成一份长期记忆笔记，只沉淀【可复用】的信息。

要求：
- 只能从对话原文中提取信息，严禁编造、补充、改写对话中不存在的事实、术语、名词、结论
- 不得对技术方案下「已验证可用」「正确」等判断，除非对话明确给出该结论
- 只保留能指导未来行动的内容：事实、结论、决策、教训、经验
- 过滤掉闲聊、情绪化表达、一次性请求、无上下文的模糊内容、无法确认的推测
- 无法确定的事不要写成结论，宁缺毋滥
- 以「## 结论」开头，列出最重要的结论/决定/教训（3-8 条，每条一句话）
- 再用「## 关键细节」列出支撑的事实与数据
- 有真正价值的未完成事项才加「## 待办/后续」，否则省略
- 只输出 Markdown 正文，不要客套话，不要代码块包裹

对话：
{transcript}"""


def _tracking_db_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return base / _TRACKING_DB


def _is_distillable_thread(thread_id: str) -> bool:
    return not thread_id.startswith(_SKIP_PREFIX) and _SKIP_FRAGMENT not in thread_id


def _safe_name(thread_id: str) -> str:
    """把 thread_id 转成安全文件名片段（保留 [A-Za-z0-9_.-]，其余替换为 _）。"""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", thread_id).strip("_")
    return name or "thread"


class ConsolidationTracker:
    """记录每个线程已蒸馏到的 checkpoint，避免重复蒸馏。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS distilled ("
            "thread_id TEXT PRIMARY KEY, checkpoint_id TEXT,"
            "note_name TEXT, distilled_at TEXT)"
        )
        self._conn.commit()

    def known(self, thread_id: str) -> tuple[str, str] | None:
        """返回 (checkpoint_id, note_name)；未蒸馏过返回 None。"""
        row = self._conn.execute(
            "SELECT checkpoint_id, note_name FROM distilled WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        return row if row else None

    def mark(self, thread_id: str, checkpoint_id: str, note_name: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO distilled"
            " (thread_id, checkpoint_id, note_name, distilled_at)"
            " VALUES (?,?,?,?)",
            (
                thread_id,
                checkpoint_id,
                note_name,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _extract_messages(checkpoint: dict[str, Any]) -> list[dict[str, str]]:
    """从 checkpoint 抽取 用户/助手 对话，丢弃工具与系统消息、空内容、相邻重复。"""
    msgs = checkpoint.get("channel_values", {}).get("messages", [])
    out: list[dict[str, str]] = []
    prev: tuple[str, str] | None = None
    for m in msgs:
        kind = getattr(m, "type", "")
        if kind in ("human", "user"):
            role = "user"
        elif kind == "ai":
            role = "assistant"
        else:
            continue  # tool / system
        text = getattr(m, "content", "")
        if isinstance(text, list):
            text = " ".join(
                b.get("text", "")
                for b in text
                if isinstance(b, dict) and b.get("type") == "text"
            )
        text = str(text).strip()
        if not text:
            continue
        pair = (role, text)
        if pair == prev:  # 相邻重复（模型/工具常回灌同一句）去重
            continue
        out.append({"role": role, "content": text})
        prev = pair
    return out


def _is_degenerate(text: str) -> bool:
    """退化输出检测：免费模型常把工具调用原文/原始 JSON 当摘要。"""
    low = text.strip().lower()
    return (
        "list_dir(" in low
        or "tool_call" in low
        or (low.startswith("{") and "}" in low and len(low) < 500)
    )


# 事实校验：笔记正文中的连续中文段，若其任意 2-gram（相邻两字）都无法在对话原文
# 中找到，则判定为「整段脑补」。2-gram 覆盖能容忍 LLM 对原文的正常重写（如
#「武汉天气」→「武汉今日天气」仍含「武汉」「天气」），只拦截凭空冒出的全新内容。
_CJK_CHUNK = re.compile(r"[\u4e00-\u9fff]+")

# 繁→简归一（最小覆盖：天气词 + 常见对话字 + 技术词），消除「對稱 vs 对称」这类
# 简繁不一致导致的误判。映射与 runtime_supervisor._simplify 保持一致思路。
_T2S = str.maketrans(
    "漢氣溫濕預幾廣東陽陰蘇寧滬慶陝龍遼烏蘭銀鄭濟長貴無錫廈門邊連島莊頭亞魯齊薩臺"
    "雞鶴馬開運樣問嗎這裡麼說對從後號機個們來時話語讀書學體風飛雲電雙條魚貝閃間際隨"
    "難壓廠應優傳傷傾備債兒兌區醫華協單賣歷歸當錄徹徵憶態懷擾擊搖損換敵數舊談誰調請"
    "諸諾講謝謠譯議護負財貢館駐騰驚骨軸鏡轉稱類適寫為於",
    "汉气温湿预几广东阳阴苏宁沪庆陕龙辽乌兰银郑济长贵无锡厦门边连岛庄头亚鲁齐萨台"
    "鸡鹤马开运样问吗这里么说对从后号机个们来时话语读书学体风飞云电双条鱼贝闪间际随"
    "难压厂应优传伤倾备债儿兑区医华协单卖历归当录彻征忆态怀扰击摇损换敌数旧谈谁调请"
    "诸诺讲谢谣译议护负财贡馆驻腾惊骨轴镜转称类适写为于",
)


def _simplify(text: str) -> str:
    return text.translate(_T2S) if text else text


# 笔记正文中「完全无法溯源」的条目达到该数量即回退确定性路径
_FABRICATION_THRESHOLD = 2


def _unknown_terms(note: str, transcript: str) -> set[str]:
    """返回笔记中【无法溯源到原文】的正文条目（只校验「- xxx」列表项）。

    简繁归一后，条目里每个连续中文段若长度 ≥3 且其任意 2-gram 都无法在原文
    中找到，即整段是原文没有的新内容（脑补），而非对原文的正常重写。
    """
    ground = _simplify(transcript)
    fabrications: set[str] = set()
    for line in note.splitlines():
        if not line.strip().startswith("-"):
            continue
        for chunk in _CJK_CHUNK.findall(_simplify(line)):
            if len(chunk) < 3:
                continue  # 太短不足以判定脑补（零星单/双字常是重写残留）
            if not any(chunk[i : i + 2] in ground for i in range(len(chunk) - 1)):
                fabrications.add(line.strip())
    return fabrications


# 确定性兜底去噪：这些是 dispatch 时的系统注入，不是用户真实提问，应从问题清单剔除
_INJECTION_MARKERS = ("[天气实事]", "用户原话")
# 确定性兜底最多保留最近 N 条去重后的真实问题，避免整段嘈杂会话原样倒出
_DETERMINISTIC_MAX_QUESTIONS = 12


def _is_meaningful_question(text: str) -> bool:
    """过滤系统注入、纯符号、超短等无意义「问题」。

    去掉标点/空白/下划线后，剩余有效字符（中文/字母/数字）不足 2 个即视为噪音。
    繁体乱码、单字输入等晦涩内容此处不做语义判断，仍会保留。
    """
    if any(m in text for m in _INJECTION_MARKERS):
        return False
    core = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return len(core) >= 2


def _distill_deterministic(pairs: list[dict[str, str]]) -> str:
    """无 LLM 时的确定性摘要：结论（最后助手答复）+ 去噪后的最近若干问题。

    不调用模型，因此绝不脑补；但会过滤系统注入、纯符号、超短消息并去重、
    截断到最近 N 条。繁简归一到简体，与 LLM 路径输出保持一致。
    """
    answers = [_simplify(p["content"]) for p in pairs if p["role"] == "assistant"]
    questions = [
        _simplify(p["content"])
        for p in pairs
        if p["role"] == "user" and _is_meaningful_question(p["content"])
    ]
    seen: set[str] = set()
    dedup: list[str] = []
    for q in questions:
        if q not in seen:
            seen.add(q)
            dedup.append(q)
    dedup = dedup[-_DETERMINISTIC_MAX_QUESTIONS:]

    parts: list[str] = []
    if answers:
        parts.append("## 结论\n\n" + answers[-1])
    if dedup:
        parts.append("## 讨论过的问题\n\n" + "\n".join(f"- {s}" for s in dedup))
    return "\n\n".join(parts) or "（会话无有效内容）"


def _distill(
    pairs: list[dict[str, str]], *, brain: Any | None, use_llm: bool = True
) -> str:
    """蒸馏：优先 LLM 摘要，失败/退化/超长时退回确定性抽取。

    长度判断基于实际喂给 LLM 的截断文本（tail_text），而非全量会话，
    否则长会话会被统一误判超长、跳过 LLM 直达确定性路径。
    """
    tail = pairs[-_LLM_TRANSCRIPT_TAIL:]
    tail_text = "\n".join(
        f"用户：{p['content']}" if p["role"] == "user" else f"助手：{p['content']}"
        for p in tail
    )
    if (
        use_llm
        and brain is not None
        and tail_text
        and len(tail_text) <= _MAX_LLM_TRANSCRIPT_CHARS
    ):
        try:
            note = brain.complete(
                _SUMMARY_PROMPT.format(transcript=tail_text),
                system="你是一个记忆整理助手，把对话浓缩成高质量长期记忆笔记。",
                temperature=0.2,
            )
            note = note.strip()
            if len(note) >= 20 and not _is_degenerate(note):
                if len(_unknown_terms(note, tail_text)) >= _FABRICATION_THRESHOLD:
                    # 术语无法溯源 → 判定脑补，回退确定性路径
                    return _distill_deterministic(pairs)
                return note
        except Exception:  # noqa: BLE001 - LLM 不可用/超时/无 key 都回退
            pass
    return _distill_deterministic(pairs)


def _list_threads(memory: Any) -> list[str]:
    """只读 sessions 库列出现有线程 id（不反序列化 checkpoint 内容）。"""
    db_path = getattr(memory, "_db_path", None)
    if db_path is None or not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
            )
        ]
    finally:
        conn.close()


async def consolidate_sessions(
    memory: Any,
    brain: Any | None = None,
    *,
    min_messages: int = _DEFAULT_MIN_MESSAGES,
    max_sessions: int = 5,
    use_llm: bool = True,
    root: Path | str | None = None,
    wiki: Any | None = None,
) -> dict[str, str]:
    """蒸馏旧会话为持久笔记，返回 {thread_id: note_name}。

    memory 必须已 setup（checkpointer 就绪，与调用方同一事件循环）。

    ``wiki``（可选，N24）：传入 ``chuan.wiki.Wiki`` 时，蒸馏产物落到 raw
    不可变层 ``sources/``（原料，不改写），而非旧的 ``notes/session-*.md``。
    不传时行为与历史一致。
    """
    checkpointer = getattr(memory, "checkpointer", None)
    if checkpointer is None:
        return {}
    tracker = ConsolidationTracker(_tracking_db_path(root))
    report: dict[str, str] = {}
    try:
        done = 0
        for thread in _list_threads(memory):
            if done >= max_sessions:
                break
            if not _is_distillable_thread(thread):
                continue
            latest = await checkpointer.aget_tuple(
                {"configurable": {"thread_id": thread}}
            )
            if latest is None:
                continue
            ckpt_id = latest.config.get("configurable", {}).get("checkpoint_id", "")
            pairs = _extract_messages(latest.checkpoint)
            if len(pairs) < min_messages:
                continue
            known = tracker.known(thread)
            if known is not None and known[0] == ckpt_id:
                continue  # checkpoint 未变，无新内容
            note = _distill(pairs, brain=brain, use_llm=use_llm)
            name = f"session-{_safe_name(thread)}"
            if wiki is not None:
                # N24：蒸馏产物落到 raw 不可变层 sources/（原料），不覆盖原文
                wiki.import_source(name, note, source=f"session:{thread}")
            else:
                memory.remember(
                    name,
                    note,
                    namespace="notes",
                    importance=3,
                    tags=["session"],
                    source=f"session:{thread}",
                )
            tracker.mark(thread, ckpt_id, name)
            report[thread] = name
            done += 1
        return report
    finally:
        tracker.close()
