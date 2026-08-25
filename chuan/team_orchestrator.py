"""N42 岗位间协作 —— 跨岗位并行编排 + 共享黑板。

「第五台阶：岗位化 1:N → 多岗位协同」：
- 1:N 已让「一个岗位」管 N 个 agent 实例（N37–N41）；
- N42 把粒度升到「一个任务拆给多个岗位并行」：用户点名或 /team 触发，
  各岗位在独立会话并行开工，共享任务级黑板（总任务 + 分工 + 各岗产出落盘），
  最后确定性汇总成一份交付。

设计取舍（对齐项目「确定性优先」教训，免费模型 JSON 不稳）:
- 显式触发为主：「让<研究>、<文案>一起<任务>」确定性解析岗位名单，不靠 LLM 猜；
- 黑板 = 磁盘真相（延续 team_state 哲学）：context.md 分工 + <role>.md 产出，
  并行各岗拿到同一份分工上下文（避免重复劳动），产出落到共享工作区（聚合/复盘/审计）；
- 并行执行复用幕僚长常驻事件循环（run_coroutine_threadsafe），真并行不阻塞；
- 汇总确定性分节（不调 LLM），与 PersonaRole._summarize 同风格。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 团队协作连接词：角色名单与任务的分界
_TEAM_CONNECTORS = ("一起", "协作", "合作", "同时", "共同", "联合")
# 角色名单分隔符
_TEAM_SPLIT_RE = re.compile(r"[、,，和与及]")
# 任务开头冗余动词（「一起做X」「一起完成X」→ X；保留实质动词如筹备/写/制作）
_TASK_STRIP_RE = re.compile(r"^(?:帮我|给我|请)?(?:做|完成|处理|搞定|整一下)\s*")

# 黑板文件名安全清洗（复用 team_state 的路径注入防护思路）。
# 注意：不含冒号——Windows 目录/文件名不允许 ":"（团队会话 id 会拼进目录名）。
_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-]")


def _safe(part: str) -> str:
    cleaned = _SAFE_ID.sub("_", part)[:60] or "role"
    return cleaned


@dataclass
class TeamAssignment:
    """单岗位分工：目标岗位 + 该岗位的职责说明。"""

    role: str          # 岗位 english 名（roster key）
    mandate: str       # 职责说明（注入该岗位的任务角度）
    display: str = ""  # 展示名（汇总/黑板标题用）


@dataclass
class TeamPlan:
    """团队协作方案：总任务 + 分工清单。"""

    task: str
    assignments: list[TeamAssignment] = field(default_factory=list)


def detect_team_roles(message: str, roster: dict[str, str]) -> TeamPlan | None:
    """解析显式多岗位意图：「让研究、文案一起筹备发布会」。

    ``roster``: english 岗位名 → 展示名（含 chief 不需要，只协作 worker）。
    返回 TeamPlan；无多岗位意图（<2 个岗位 / 无连接词）返回 None。

    确定性解析（不调 LLM）：
    1. 去掉「请让/让/请」前缀
    2. 找连接词（一起/协作/…），前面=角色名单，后面=任务
    3. 名单按分隔符拆分，逐个匹配 roster（english 或展示名）
    4. ≥2 个不同岗位 → 成立
    """
    text = message.strip()
    for pre in ("请让", "让", "请"):
        if text.startswith(pre):
            text = text[len(pre):]
            break
    head: str | None = None
    task: str = ""
    for conn in _TEAM_CONNECTORS:
        if conn in text:
            head, _, task = text.partition(conn)
            break
    if head is None or not task.strip():
        return None
    # 反向查找表：展示名 / english 名 → english 名
    lookup: dict[str, str] = {}
    for eng, disp in roster.items():
        lookup[eng] = eng
        if disp and disp != eng:
            lookup[disp] = eng
    matched: list[str] = []
    for token in _TEAM_SPLIT_RE.split(head):
        eng = lookup.get(token.strip())
        if eng is not None and eng not in matched:
            matched.append(eng)
    if len(matched) < 2:
        return None
    clean_task = _TASK_STRIP_RE.sub("", task.strip())
    clean_task = clean_task.strip() or task.strip()
    return TeamPlan(
        task=clean_task,
        assignments=[
            TeamAssignment(role=eng, mandate=roster[eng], display=roster[eng])
            for eng in matched
        ],
    )


class TeamBlackboard:
    """任务级共享黑板（磁盘真相）：data/teams/<session>/backboard/。

    布局:
        context.md   —— 总任务 + 分工清单（开工时写，各岗共享同一份）
        <role>.md    —— 各岗位产出（完成后写，聚合/复盘/审计用）
    写失败静默（旁路，不阻断协作执行）。
    """

    def __init__(
        self,
        task: str,
        assignments: list[TeamAssignment],
        session_id: str = "team",
        root: Path | str | None = None,
    ) -> None:
        if root is not None:
            base = Path(root)
        else:
            base = Path(__file__).resolve().parent.parent / "data"
        self._dir = base / "teams" / _safe(session_id) / "backboard"
        self._task = task
        self._assignments = assignments

    def _ensure(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def write_context(self) -> None:
        """写入总任务 + 分工（各岗位共享的上下文）。"""
        lines = [f"# 团队任务\n{self._task}\n", "## 分工"]
        for a in self._assignments:
            lines.append(f"- {a.display or a.role}：{a.mandate}")
        self._write("context.md", "\n".join(lines))

    def write_result(self, role: str, content: str, success: bool) -> None:
        """写入某岗位的产出（黑板 <role>.md）。"""
        mark = "" if success else "（失败）"
        self._write(f"{_safe(role)}.md", f"## {role}{mark}\n{content}")

    def _write(self, name: str, text: str) -> None:
        self._ensure()
        try:
            (self._dir / name).write_text(text, encoding="utf-8")
        except OSError:
            pass

    @property
    def dir(self) -> Path:
        return self._dir


class TeamOrchestrator:
    """跨岗位协同编排器：一任务拆多岗并行 + 共享黑板 + 确定性汇总。

    复用幕僚长的常驻事件循环（``sup._loop``）：各岗位 ``role.dispatch``
    经 ``run_coroutine_threadsafe`` 并行调度（同一循环内交错 = 真并行）。
    """

    def __init__(self, sup: Any, root: Path | str | None = None) -> None:
        self._sup = sup
        self._root = root
        self._loop = getattr(sup, "_loop", None)

    # ------------------------------------------------------------------ #
    # 公开接口
    # ------------------------------------------------------------------ #
    def roster(self) -> dict[str, str]:
        """可协作岗位：english 名 → 展示名（worker，不含幕僚长）。"""
        out: dict[str, str] = {}
        for name, role in getattr(self._sup, "_workers", {}).items():
            out[name] = getattr(role, "display_name", name)
        return out

    def orchestrate(
        self,
        plan: TeamPlan,
        session_id: str = "team",
    ) -> str:
        """执行团队协作：并行派发各岗位 → 落黑板 → 汇总。

        Returns:
            分节汇总字符串（各岗位产出 + 成败标注）
        """
        if not plan.assignments:
            return "[团队协作] 没有可派发的岗位。"
        if self._loop is None or getattr(self._sup, "_is_awake", False) is False:
            return "[团队协作] 幕僚长未就绪，无法并行派发。"

        blackboard = TeamBlackboard(
            plan.task, plan.assignments,
            session_id=session_id, root=self._root,
        )
        blackboard.write_context()

        # 各岗位独立会话 + 共享黑板上下文注入；全部先调度（并行），再逐个取结果
        results: list[tuple[TeamAssignment, str, bool]] = []
        pending: list[tuple[TeamAssignment, Any]] = []
        for a in plan.assignments:
            role = self._sup._workers.get(a.role)
            if role is None:
                results.append((a, f"[岗位 {a.role} 不可用]", False))
                continue
            prompt = self._subtask_prompt(plan, a, blackboard)
            pending.append((a, self._run_dispatch(a, role, prompt, session_id)))
        for a, fut in pending:
            content, success = self._await_result(a, fut)
            blackboard.write_result(a.role, content, success)
            results.append((a, content, success))
        return self._summarize(plan, results)

    # ------------------------------------------------------------------ #
    # LLM 选岗拆分（/team <任务> 用）：严格校验，失败回 None
    # ------------------------------------------------------------------ #
    def plan_team_llm(self, task: str, model: Any) -> TeamPlan | None:
        """用规划模型选 2-4 个岗位并分配职责（/team 自动拆）。

        ``/team <任务>`` 未点名岗位时走此路径。模型不可用 / JSON 非法 /
        岗位名不在 roster / 拆不出 ≥2 岗 → 返回 None（调用方兜底单岗位）。
        """
        if self._loop is None or model is None:
            return None
        fut = asyncio.run_coroutine_threadsafe(
            self._plan_team_llm_async(task, model), self._loop
        )
        try:
            return fut.result(timeout=120)
        except Exception:  # noqa: BLE001 - 选岗失败兜底单岗位
            return None

    async def _plan_team_llm_async(self, task: str, model: Any) -> TeamPlan | None:
        roster = self.roster()
        names = "、".join(f"{d or e}（{e}）" for e, d in roster.items())
        prompt = (
            "你是团队任务规划器。下面的任务需要拆给多个岗位并行完成。\n"
            f"可用岗位：{names}\n"
            "请选 2-4 个最匹配的岗位，为每个写一句职责（该岗位在本任务中负责的部分视角）。\n"
            "只输出 JSON，不要任何其他文字：\n"
            '{"task": "统一后的任务描述", "assignments": ['
            '{"role": "岗位英文名", "mandate": "该岗位职责"}]}\n'
            f"任务：{task}"
        )
        try:
            resp = await model.ainvoke(prompt)
        except Exception:  # noqa: BLE001 - 模型失败兜底单岗位
            return None
        raw = str(getattr(resp, "content", None) or resp)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return None
        assignments: list[TeamAssignment] = []
        for item in data.get("assignments") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            mandate = str(item.get("mandate") or "").strip()
            if role in roster and mandate and role not in [
                a.role for a in assignments
            ]:
                assignments.append(
                    TeamAssignment(role=role, mandate=mandate, display=roster[role])
                )
        if len(assignments) < 2:
            return None
        clean = str(data.get("task") or "").strip()
        return TeamPlan(task=clean or task, assignments=assignments)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _subtask_prompt(
        self, plan: TeamPlan, a: TeamAssignment, blackboard: TeamBlackboard
    ) -> str:
        """构造该岗位的任务提示：团队总任务 + 分工 + 黑板位置。"""
        others = "、".join(
            x.display or x.role for x in plan.assignments if x.role != a.role
        ) or "无"
        return (
            f"【团队任务】{plan.task}\n\n"
            f"【你的岗位】{a.display or a.role}\n"
            f"【你的职责】请以「{a.display or a.role}」的领域视角负责本任务中"
            f"属于你的部分，产出完整结果。\n"
            f"【团队分工】其他岗位正在并行处理：{others}。请专注你的职责，"
            f"不要重复他人工作，也不要要求他人代劳。\n"
            f"【黑板】协作产出会写入共享黑板：{blackboard.dir}"
        )

    def _run_dispatch(
        self, a: TeamAssignment, role: Any, prompt: str, session_id: str
    ) -> Any:
        """把单岗位 dispatch 调度到事件循环（并行执行），返回 asyncio Future。"""
        return asyncio.run_coroutine_threadsafe(
            role.dispatch(
                prompt,
                # 下划线分隔（不用冒号）：session_id 会流进 TeamStateWriter 文件名，
                # Windows 文件名不允许 ":"
                session_id=f"team_{_safe(session_id)}_{_safe(a.role)}",
            ),
            self._loop,
        )

    def _await_result(self, a: TeamAssignment, fut: Any) -> tuple[str, bool]:
        """同步等待单岗位 dispatch 结果；异常/失败标记为失败产出。"""
        try:
            reply = fut.result(timeout=600)
        except Exception as exc:  # noqa: BLE001 - 单岗位失败不阻断整个协作
            return f"[岗位 {a.role} 执行失败: {exc}]", False
        content = str(reply)
        success = "执行失败" not in content
        # 只剥 dispatch 返回的「[角色名]」包装前缀（display 或 english），
        # 不误伤产出正文里的其他方括号标签（如 [数据]、[Important]）
        for tag in (a.display, a.role):
            if tag and content.startswith(f"[{tag}]"):
                content = content[len(f"[{tag}]"):].strip()
                break
        return content, success

    @staticmethod
    def _summarize(
        plan: TeamPlan, results: list[tuple[TeamAssignment, str, bool]]
    ) -> str:
        """确定性汇总：各岗位产出分节 + 成败标注（不调 LLM）。"""
        sections: list[str] = []
        for a, content, success in results:
            mark = "" if success else "（失败）"
            sections.append(f"### [{a.display or a.role}]{mark}\n{content}")
        head = f"团队协作完成：{plan.task}\n"
        return head + "\n\n".join(sections)
