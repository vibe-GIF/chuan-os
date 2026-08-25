"""向量 RAG 评估闸门 —— 量化记忆库规模，判定是否触发本地 embedding+faiss 评估。

ROADMAP P3 待办（2026-08-24 RAG 可行性评估）的触发条件：
**内部+外接库合计 > 1000 篇 / 100 万字符，且出现「关键词漏召回」具体案例**，
才评估本地 embedding+faiss（faiss 1.15 已装、缺 sentence-transformers/torch）。

本模块是**确定性闸门**（不依赖 LLM，对齐项目惯例）：
- 统计内部 notes/ 与外接库的 .md 篇数 + 字符数；
- 对比阈值（缺省 1000 篇 / 100 万字符，模块常量可测）；
- 追踪漏召回案例（data/memory/rag_missed_cases.md，人工/自动追加）；
- 未触发 → 明确建议继续 FTS5；触发 → 输出下一步评估清单。

任何统计项失败静默降级为 0，绝不抛错；被 skills/rag_gate.yaml 引用，
经 SkillRegistry 包装为 LangChain Tool。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

# 项目根（skills/handlers/rag_gate.py → skills/handlers → skills → 根）
_ROOT = Path(__file__).resolve().parent.parent.parent

# 触发阈值（缺省；测试用 monkeypatch 调小以快速构造触发态）
_DOC_THRESHOLD = 1000
_CHAR_THRESHOLD = 1_000_000

# 漏召回案例文件（磁盘真相，追加式）
_MISSED_CASES = "rag_missed_cases.md"

_GATE_NOT_HIT = "未触发"
_GATE_PARTIAL = "规模达标，待案例"
_GATE_HIT = "触发"


def _count_md(root: Path) -> tuple[int, int]:
    """统计目录下 .md 文档（篇数, 字符数）；目录不存在返回 (0, 0)。"""
    try:
        if not root.exists() or not root.is_dir():
            return 0, 0
        docs, chars = 0, 0
        for p in root.rglob("*.md"):
            try:
                if not p.is_file():
                    continue
                docs += 1
                chars += p.stat().st_size
            except OSError:  # noqa: BLE001 - 单文件失败跳过
                continue
        return docs, chars
    except Exception:  # noqa: BLE001 - 统计失败静默降级
        return 0, 0


def _resolve_external_vaults(config_path: str | Path = "") -> list[tuple[str, Path]]:
    """读 config.yaml 的 memory.external_vaults（name, Path）；缺省/失败返回空。"""
    p = Path(config_path) if config_path else _ROOT / "config" / "config.yaml"
    if not p.is_absolute():
        p = _ROOT / p
    out: list[tuple[str, Path]] = []
    if not p.exists():
        return out
    try:
        import yaml

        data = yaml.safe_load(p.open("r", encoding="utf-8")) or {}
        for item in data.get("memory", {}).get("external_vaults", []) or []:
            name = str(item.get("name", "")).strip()
            raw = str(item.get("path", "")).strip()
            if not name or not raw:
                continue
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else (_ROOT / candidate)
            out.append((name, path))
    except Exception:  # noqa: BLE001 - 配置读不到按空
        out = []
    return out


def _cases_path(vault_path: Path) -> Path:
    return vault_path / _MISSED_CASES


def _count_cases(cases_file: Path) -> int:
    """漏召回案例数（非空行计数）；文件不存在/失败返回 0。"""
    try:
        if not cases_file.exists():
            return 0
        return sum(1 for line in cases_file.open("r", encoding="utf-8") if line.strip())
    except Exception:  # noqa: BLE001
        return 0


def _default_vault() -> Path:
    return _ROOT / "data" / "memory" / "vault"


def record_missed_case(query: str, note: str = "", vault_path: str | Path = "") -> str:
    """记录一个「关键词漏召回」具体案例（append 到 rag_missed_cases.md）。

    Returns:
        成功返回写入摘要文本；失败返回可读降级提示（不抛错）。
    """
    try:
        root = Path(vault_path) if vault_path else _default_vault()
        root.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"- [{stamp}] query: {query.strip() or '(未填写)'}"
        if note and note.strip():
            line += f" · 说明: {note.strip()}"
        path = _cases_path(root)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return f"已记录漏召回案例 → {path}"
    except Exception as exc:  # noqa: BLE001
        return f"（记录漏召回案例失败：{exc}）"


def rag_gate(vault_path: str = "") -> str:
    """向量 RAG 评估闸门：量化记忆库规模并判定是否触发本地 embedding+faiss 评估。

    Args:
        vault_path: 内部记忆 vault 根（缺省 data/memory/vault；测试可注入）。

    Returns:
        可读评估报告文本（确定性，不抛错）。
    """
    root = Path(vault_path) if vault_path else _default_vault()
    notes = root / "notes"

    int_docs, int_chars = _count_md(notes)
    ext_lines: list[str] = []
    ext_docs = ext_chars = 0
    for name, path in _resolve_external_vaults():
        d, c = _count_md(path)
        ext_docs += d
        ext_chars += c
        ext_lines.append(f"    - 外接库「{name}」: {d} 篇 / {c} 字符（{path}）")

    total_docs = int_docs + ext_docs
    total_chars = int_chars + ext_chars
    cases = _count_cases(_cases_path(root))

    size_hit = total_docs > _DOC_THRESHOLD and total_chars > _CHAR_THRESHOLD
    if size_hit and cases > 0:
        status = _GATE_HIT
        verdict = (
            "✅ **触发**：记忆规模已超阈值且有漏召回案例，建议启动本地 embedding+faiss 评估。\n"
            "    下一步清单：① 装 sentence-transformers/torch；② 评估嵌入模型（bge-m3 等）；"
            "③ 建向量索引做双路合并召回对比；④ 用已记录案例验证召回率提升。"
        )
    elif size_hit:
        status = _GATE_PARTIAL
        verdict = "规模已达标，但暂无「关键词漏召回」具体案例——暂不启动，继续 FTS5 即可。"
    else:
        status = _GATE_NOT_HIT
        verdict = "规模未达阈值——暂不启动本地向量评估，继续 FTS5 词法检索即可。"

    head = (
        f"向量 RAG 评估闸门（P3）\n"
        f"  状态: {status}\n"
        f"  内部 notes/: {int_docs} 篇 / {int_chars} 字符\n"
    )
    if ext_lines:
        head += "  外接库:\n" + "\n".join(ext_lines) + "\n"
    else:
        head += "  外接库: 未配置或不可用\n"
    head += (
        f"  合计: {total_docs} 篇 / {total_chars} 字符"
        f"（阈值 {_DOC_THRESHOLD} 篇 / {_CHAR_THRESHOLD} 字符）\n"
        f"  漏召回案例: {cases} 条\n\n"
        f"{verdict}"
    )
    return head
