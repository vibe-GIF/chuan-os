"""L4 记忆闭环 —— 旧会话巩固（consolidation worker）测试。"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from chuan.consolidation import (
    ConsolidationTracker,
    _distill,
    _distill_deterministic,
    _extract_messages,
    _is_degenerate,
    _is_distillable_thread,
    _unknown_terms,
    consolidate_sessions,
)
from chuan.memory import Memory


def _msg(kind: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(type=kind, content=text)


# ------------------------------------------------------------------ #
# 纯函数
# ------------------------------------------------------------------ #
def test_extract_messages_filters_tools_and_dedup() -> None:
    checkpoint = {
        "channel_values": {
            "messages": [
                _msg("human", "你好"),
                _msg("ai", "收到"),
                _msg("tool", "list_dir('x')"),  # 工具消息丢弃
                _msg("system", "系统提示"),  # 系统消息丢弃
                _msg("human", ""),  # 空内容丢弃
                _msg("ai", "收到"),  # 与上一条助手相同 → 去重
                _msg("human", "武汉天气如何？"),
                _msg("ai", "今天武汉晴朗。"),
            ]
        }
    }
    pairs = _extract_messages(checkpoint)
    assert [p["role"] for p in pairs] == ["user", "assistant", "user", "assistant"]
    assert pairs[0]["content"] == "你好"
    assert "list_dir" not in " ".join(p["content"] for p in pairs)


def test_extract_messages_handles_content_list() -> None:
    checkpoint = {
        "channel_values": {
            "messages": [
                SimpleNamespace(
                    type="ai",
                    content=[
                        {"type": "text", "text": "结论"},
                        {"type": "tool_use", "name": "get_weather"},
                    ],
                )
            ]
        }
    }
    pairs = _extract_messages(checkpoint)
    assert pairs == [{"role": "assistant", "content": "结论"}]


@pytest.mark.parametrize(
    "thread_id,distillable",
    [
        ("default", True),
        ("abc123", True),
        ("ask:researcher", False),
        ("main:plan:xyz", False),
    ],
)
def test_is_distillable_thread(thread_id: str, distillable: bool) -> None:
    assert _is_distillable_thread(thread_id) is distillable


def test_distill_deterministic_structure() -> None:
    pairs = [
        {"role": "user", "content": "帮我整理部署流程"},
        {"role": "assistant", "content": "结论：先跑测试再部署。"},
    ]
    note = _distill_deterministic(pairs)
    assert "## 结论" in note
    assert "先跑测试再部署" in note
    assert "## 讨论过的问题" in note
    assert "帮我整理部署流程" in note


def test_distill_deterministic_empty() -> None:
    assert _distill_deterministic([]) == "（会话无有效内容）"


def test_distill_deterministic_filters_injection_and_noise() -> None:
    pairs = [
        {"role": "user", "content": "武汉天气"},
        {"role": "assistant", "content": "武汉晴朗。"},
        # 系统注入（天气实事）应被剔除
        {"role": "user", "content": "[天气实事] Wuhan：Clear 温度28°C\n用户原话：武汉天气"},
        {"role": "assistant", "content": "武汉晴朗。"},
        # 纯符号/超短应被剔除
        {"role": "user", "content": "!"},
        # 真实提问（繁体）应保留并归一
        {"role": "user", "content": "武漢明天適合穿什麼"},
        {"role": "assistant", "content": "建议穿薄外套。"},
    ]
    note = _distill_deterministic(pairs)
    assert "[天气实事]" not in note
    assert "用户原话" not in note
    assert "武汉明天适合穿什么" in note  # 繁体已归一为简体
    assert "!" not in note  # 纯符号被剔除


def test_distill_deterministic_dedup_and_truncate() -> None:
    pairs = [
        {"role": "user", "content": f"问题{i}"}
        for i in range(50)
    ]
    note = _distill_deterministic(pairs)
    # 只保留最近 12 条
    lines = [l for l in note.splitlines() if l.startswith("- ")]
    assert len(lines) == 12
    assert "问题49" in note
    assert "问题0" not in note


@pytest.mark.parametrize(
    "text,bad",
    [
        ("list_dir('.') 的输出是 x", True),
        ("工具调用 tool_call 结果", True),
        ('{"conclusion": "x"}', True),
        ("这是一份正常的结论笔记。", False),
    ],
)
def test_is_degenerate(text: str, bad: bool) -> None:
    assert _is_degenerate(text) is bad


def test_distill_falls_back_to_deterministic_on_degenerate() -> None:
    pairs = [
        {"role": "user", "content": "Q"},
        {"role": "assistant", "content": "A"},
    ]

    class DegenerateBrain:
        def complete(self, *_a: object, **_k: object) -> str:
            return "list_dir('.')"

    note = _distill(pairs, brain=DegenerateBrain(), use_llm=True)
    assert "## 结论" in note  # 退化时回退确定性抽取


def test_distill_llm_short_output_falls_back() -> None:
    pairs = [
        {"role": "user", "content": "Q"},
        {"role": "assistant", "content": "A"},
    ]

    class ShortBrain:
        def complete(self, *_a: object, **_k: object) -> str:
            return "短"  # 长度不足 20 → 回退

    assert _distill(pairs, brain=ShortBrain(), use_llm=True) != "短"


def test_distill_falls_back_on_fabricated_terms() -> None:
    """笔记中出现原文没有的全新主题（整段脑补）→ 回退确定性路径。"""
    pairs = [
        {"role": "user", "content": "武汉今天天气如何？"},
        {"role": "assistant", "content": "武汉今天晴朗，28度。"},
    ]

    class FabBrain:
        def complete(self, *_a: object, **_k: object) -> str:
            # 「对称类型」「中心对称」是原文完全没有的新主题
            return (
                "## 结论\n- 武汉今天晴朗。\n"
                "- 对称类型包括旋转对称与镜像对称。\n"
                "- 中心对称需要旋转 180 度。\n"
            )

    note = _distill(pairs, brain=FabBrain(), use_llm=True)
    assert "## 结论" in note
    assert "武汉今天晴朗" in note  # 回退到确定性结论（最后助手答复）
    assert "对称" not in note


def test_unknown_terms_flags_new_topic_only() -> None:
    transcript = "武汉今天晴朗，28度。"
    note = (
        "## 结论\n"
        "- 武汉今天晴朗。\n"  # 正常重写 → 不标记
        "- 对称类型含旋转对称。\n"  # 全新主题 → 标记
    )
    assert _unknown_terms(note, transcript) == {"- 对称类型含旋转对称。"}


def test_unknown_terms_tolerates_rewording() -> None:
    transcript = "武汉今天晴朗，温度28度，湿度76%。"
    note = "- 武汉今日天气晴朗，气温28度。\n"
    assert _unknown_terms(note, transcript) == set()


def test_unknown_terms_simplifies_traditional() -> None:
    # 原文繁体「對稱」、笔记简体「对称」→ 归一后视为可溯源，不误判脑补
    transcript = "你問對稱是什麼意思？"
    note = "- 对称是指沿直线折叠重合。\n"
    assert _unknown_terms(note, transcript) == set()


def test_unknown_terms_ignores_headers_and_frontmatter() -> None:
    transcript = "武汉晴朗。"
    note = "---\nimportance: 3\n---\n\n# 大标题\n## 结论\n- 武汉晴朗。\n"
    assert _unknown_terms(note, transcript) == set()


# ------------------------------------------------------------------ #
# ConsolidationTracker
# ------------------------------------------------------------------ #
def test_tracker_mark_and_known(tmp_path: Path) -> None:
    db = tmp_path / "consolidation.db"
    tracker = ConsolidationTracker(db)
    try:
        assert tracker.known("t1") is None
        tracker.mark("t1", "ckpt-a", "session-t1")
        assert tracker.known("t1") == ("ckpt-a", "session-t1")
        # 覆盖更新
        tracker.mark("t1", "ckpt-b", "session-t1")
        assert tracker.known("t1") == ("ckpt-b", "session-t1")
    finally:
        tracker.close()


# ------------------------------------------------------------------ #
# 端到端：consolidate_sessions
# ------------------------------------------------------------------ #
class FakeCheckpointer:
    """返回固定 checkpoint 的假 checkpointer（只测蒸馏路径）。"""

    def __init__(self, checkpoint: dict) -> None:
        self._checkpoint = checkpoint

    async def aget_tuple(self, config: dict) -> SimpleNamespace:
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": "ckpt-1"}},
            checkpoint=self._checkpoint,
        )


def _make_memory(tmp_path: Path) -> Memory:
    memory = Memory(vault_path=tmp_path / "vault")
    memory._db_path = tmp_path / "sessions.db"  # 隔离测试库
    return memory


def _seed_thread(db_path: Path, thread_id: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT)")
        conn.execute("INSERT INTO checkpoints (thread_id) VALUES (?)", (thread_id,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_consolidate_sessions_writes_notes(tmp_path: Path) -> None:
    memory = _make_memory(tmp_path)
    _seed_thread(memory._db_path, "thread-1")
    memory.checkpointer = FakeCheckpointer(
        {
            "channel_values": {
                "messages": [
                    _msg("human", "帮我整理部署流程"),
                    _msg("ai", "结论：先跑测试再部署，注意回滚。"),
                    _msg("human", "有没有注意事项？"),
                    _msg("ai", "有：备份数据库后操作。"),
                ]
            }
        }
    )

    report = await consolidate_sessions(
        memory, brain=None, use_llm=False, root=tmp_path
    )
    assert report == {"thread-1": "session-thread-1"}
    note = tmp_path / "vault" / "notes" / "session-thread-1.md"
    assert note.exists()
    raw = note.read_text(encoding="utf-8")
    assert "## 结论" in raw
    # 确定性摘要：结论取最后一条助手回复
    assert "有：备份数据库后操作" in raw
    assert "## 讨论过的问题" in raw
    # 元数据写入
    assert "importance: 3" in raw
    assert "session" in raw


@pytest.mark.asyncio
async def test_consolidate_skips_already_distilled(tmp_path: Path) -> None:
    memory = _make_memory(tmp_path)
    _seed_thread(memory._db_path, "thread-1")
    memory.checkpointer = FakeCheckpointer(
        {
            "channel_values": {
                "messages": [
                    _msg("human", "Q1"),
                    _msg("ai", "A1"),
                    _msg("human", "Q2"),
                    _msg("ai", "A2"),
                ]
            }
        }
    )

    first = await consolidate_sessions(memory, use_llm=False, root=tmp_path)
    assert len(first) == 1
    # checkpoint 未变 → 第二次跳过
    second = await consolidate_sessions(memory, use_llm=False, root=tmp_path)
    assert second == {}


@pytest.mark.asyncio
async def test_consolidate_skips_short_and_non_distillable(tmp_path: Path) -> None:
    memory = _make_memory(tmp_path)
    _seed_thread(memory._db_path, "ask:researcher")  # 协作线程
    _seed_thread(memory._db_path, "short-1")  # 消息不足
    memory.checkpointer = FakeCheckpointer(
        {
            "channel_values": {
                "messages": [
                    _msg("human", "只有一问"),
                    _msg("ai", "只有一答"),
                ]
            }
        }
    )
    report = await consolidate_sessions(
        memory, use_llm=False, root=tmp_path, min_messages=4
    )
    assert report == {}


@pytest.mark.asyncio
async def test_consolidate_no_checkpointer_returns_empty(tmp_path: Path) -> None:
    memory = _make_memory(tmp_path)
    assert await consolidate_sessions(memory, use_llm=False, root=tmp_path) == {}


@pytest.mark.asyncio
async def test_consolidate_respects_max_sessions(tmp_path: Path) -> None:
    memory = _make_memory(tmp_path)
    for tid in ("t1", "t2", "t3"):
        _seed_thread(memory._db_path, tid)
    memory.checkpointer = FakeCheckpointer(
        {
            "channel_values": {
                "messages": [
                    _msg("human", f"问题{i}"),
                    _msg("ai", f"回答{i}"),
                    _msg("human", f"问题{i}b"),
                    _msg("ai", f"回答{i}b"),
                ]
                for i in range(4)
            }
        }
    )
    report = await consolidate_sessions(
        memory, use_llm=False, root=tmp_path, max_sessions=2
    )
    assert len(report) == 2
