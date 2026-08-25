"""P1 监督者（SupervisorMonitor）—— 全程监控 worker 执行轨迹，发现死胡同就 redirect。

借鉴 NVIDIA AVO 的 Supervisor：不干活，只看轨迹（像 CEO）。幕僚长只做初始路由，
本组件补「全程监控」——记录每次执行轨迹，确定性检测死胡同（反复失败 / 循环 / 停滞），
并给出 redirect 决策（换 agent / 注入新思路 / 中止），防止循环浪费。

设计约束（对齐项目教训）：
- 全确定性，不依赖 LLM —— 免费模型工具调用不稳定，关键判断必须可复现、可测试
- 旁路设计：监控与 redirect 是增强层，任何异常/误判都不阻断主流程
- 轨迹 = 执行路径：role + step（子任务/单步）+ attempt + agent + 结果内容
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass
class TrajectoryStep:
    """轨迹上的一步：一次 agent 执行尝试。"""

    step: str
    attempt: int
    agent: str
    success: bool
    content: str
    started_at: float
    duration: float
    hint: str = ""


@dataclass
class RedirectDecision:
    """监督者的 redirect 决策。

    kind:
        abort        终止重试，按失败结算（避免继续烧 token/时间）
        switch_agent 换一个 agent 重试（当前 agent 已在死胡同里）
        inject_hint  同一 agent 换思路重试（提示语注入下一步）
    """

    kind: str  # "abort" | "switch_agent" | "inject_hint"
    reason: str
    target_agent: str | None = None
    hint: str | None = None

    @property
    def is_abort(self) -> bool:
        return self.kind == "abort"


# 换思路提示：注入重试提示的开头，引导完全不同的做法
_REDIRECT_HINT = (
    "【监督者提示】前一次尝试未成功（判为死胡同）。请完全换一种思路："
    "不要重复之前的步骤和工具，先分析为什么失败，再给出不同的做法。"
)


def _ngrams(text: str) -> set[str]:
    """字符 2-gram 集合；短串退化为整串单 token（CJK 友好）。"""
    text = (text or "").strip().lower()
    if not text:
        return set()
    if len(text) < 2:
        return {text}
    return {text[i:i + 2] for i in range(len(text) - 1)}


class SupervisorMonitor:
    """监督者 —— 执行轨迹记录 + 死胡同检测 + redirect 指导。

    生命周期由 PersonaRole / RuntimeSupervisor 驱动：
        monitor.start_trace(role, trace_id)
        monitor.record_step(...)          # 每次 agent 尝试后
        monitor.check_dead_end(...)       # 重试前查询是否死胡同 → redirect
        monitor.finish_trace(trace_id)
    """

    # 死胡同判定阈值（可构造覆盖，测试用）
    FAIL_THRESHOLD = 2        # 连续失败 ≥N 次且结果相似 → 反复失败
    LOOP_THRESHOLD = 2        # 连续 ≥N 次输出高度相似（≥0.95）→ 循环
    MAX_FAIL_ATTEMPTS = 3     # 连续失败 ≥N 次（不管相似性）→ 耗尽即判死
    STAGNATION_SECS = 120.0   # 轨迹存活超过 N 秒未结束 → 停滞

    def __init__(
        self,
        *,
        fail_threshold: int | None = None,
        loop_threshold: int | None = None,
        max_fail_attempts: int | None = None,
        stagnation_secs: float | None = None,
        max_traces: int = 200,
    ) -> None:
        self.fail_threshold = fail_threshold or self.FAIL_THRESHOLD
        self.loop_threshold = loop_threshold or self.LOOP_THRESHOLD
        self.max_fail_attempts = max_fail_attempts or self.MAX_FAIL_ATTEMPTS
        self.stagnation_secs = stagnation_secs or self.STAGNATION_SECS
        self.max_traces = max_traces
        # trace_id → {role, started_at, status, steps: [TrajectoryStep]}
        self._traces: dict[str, dict[str, Any]] = {}
        # 已判定死胡同 / 已应用 redirect（供 /monitor 面板展示）
        self._dead_ends: list[dict[str, Any]] = []
        self._redirects: list[dict[str, Any]] = []
        self._lock = RLock()

    # ------------------------------------------------------------------ #
    # 轨迹生命周期
    # ------------------------------------------------------------------ #
    def start_trace(self, role: str, trace_id: str) -> None:
        """开启一条执行轨迹（每次 dispatch 一条）。"""
        with self._lock:
            if trace_id in self._traces:
                return  # 复用已存在轨迹（同会话多次 dispatch 叠加记录）
            self._traces[trace_id] = {
                "role": role,
                "started_at": time.time(),
                "status": "active",
                "steps": [],
            }
            self._trim_finished()

    def finish_trace(self, trace_id: str) -> None:
        """标记轨迹结束（dispatch 完成）。"""
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is not None and trace["status"] == "active":
                trace["status"] = "done"

    def trace_elapsed(self, trace_id: str) -> float:
        with self._lock:
            trace = self._traces.get(trace_id)
            return time.time() - trace["started_at"] if trace else 0.0

    # ------------------------------------------------------------------ #
    # 步骤记录
    # ------------------------------------------------------------------ #
    def record_step(
        self,
        trace_id: str,
        step: str,
        *,
        attempt: int,
        agent: str,
        success: bool,
        content: str,
        duration: float,
        hint: str = "",
    ) -> None:
        """记录一次 agent 尝试到轨迹。"""
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return
            trace["steps"].append(
                TrajectoryStep(
                    step=step, attempt=attempt, agent=agent, success=success,
                    content=content, started_at=time.time(), duration=duration,
                    hint=hint,
                )
            )

    # ------------------------------------------------------------------ #
    # 死胡同检测 → redirect 决策
    # ------------------------------------------------------------------ #
    def check_dead_end(
        self,
        trace_id: str,
        step: str,
        *,
        retries_left: int,
        current_agent: str,
        available: list[str] | None = None,
    ) -> RedirectDecision | None:
        """重试前检查该步历史尝试，返回死胡同 redirect 决策；无死胡同返回 None。

        Args:
            trace_id: 轨迹 ID
            step: 步骤标识（子任务 id）
            retries_left: 剩余可重试次数（含本次），≤0 表示已到最后一次
            current_agent: 当前执行 agent（用于决定是否换 agent）
            available: 可选的其它 agent 名单（常驻池），用于 switch_agent
        """
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return None
            steps = sorted(
                (s for s in trace["steps"] if s.step == step),
                key=lambda s: s.attempt,
            )
        candidates = [
            a for a in (available or []) if a and a != current_agent
        ]

        # 循环 / 反复失败 需要 ≥2 步的信号才判定，单步绝不误判
        if len(steps) >= 2:
            last, prev = steps[-1], steps[-2]

            # 1) 循环：连续输出高度相似（哪怕标了 success 也在原地打转）
            if self._similar(last.content, prev.content, 0.95):
                reason = f"循环（'<{step}>' 连续输出相同，重试同 prompt 只会重复浪费）"
                decision = self._redirect(
                    reason, retries_left, current_agent, candidates,
                    prefer_switch=True,
                )
                self._note_dead_end(trace_id, step, "loop", reason, decision)
                return decision

            # 2) 反复失败：连续失败 + 结果相似，或尝试耗尽
            consec_fail = 0
            for s in reversed(steps):
                if s.success:
                    break
                consec_fail += 1
            similar_fails = (
                consec_fail >= self.fail_threshold
                and self._similar(steps[-1].content, steps[-2].content, 0.7)
            )
            exhausted = consec_fail >= self.max_fail_attempts
            if similar_fails or exhausted:
                reason = (
                    "反复失败（结果相似）" if similar_fails
                    else f"反复失败（连续 {consec_fail} 次，尝试耗尽）"
                )
                decision = self._redirect(
                    reason, retries_left, current_agent, candidates,
                    prefer_switch=False,
                )
                self._note_dead_end(trace_id, step, "repeated_failure", reason, decision)
                return decision

        # 3) 停滞：轨迹存活过久（watchdog，单步也可能长时间悬挂）
        if self.trace_elapsed(trace_id) > self.stagnation_secs:
            reason = f"停滞（轨迹存活超过 {self.stagnation_secs:.0f}s 未结束）"
            decision = self._redirect(
                reason, retries_left, current_agent, candidates,
                prefer_switch=True,
            )
            self._note_dead_end(trace_id, step, "stagnation", reason, decision)
            return decision

        return None

    def _redirect(
        self,
        reason: str,
        retries_left: int,
        current_agent: str,
        candidates: list[str],
        *,
        prefer_switch: bool,
    ) -> RedirectDecision:
        """按「剩余次数 + 可用 agent」产出 redirect 决策。"""
        # 循环/停滞：同 prompt 必重复，没别的 agent 就直接中止，不再空耗
        if prefer_switch and not candidates:
            return RedirectDecision("abort", reason)
        if candidates:
            return RedirectDecision(
                "switch_agent", reason, target_agent=candidates[0]
            )
        if retries_left <= 0:
            return RedirectDecision("abort", reason)
        return RedirectDecision("inject_hint", reason, hint=_REDIRECT_HINT)

    def record_redirect(
        self, trace_id: str, step: str, decision: RedirectDecision
    ) -> None:
        """记录一条已应用的 redirect（幂等，供面板展示）。"""
        with self._lock:
            self._redirects.append({
                "trace_id": trace_id,
                "step": step,
                "kind": decision.kind,
                "reason": decision.reason,
                "target_agent": decision.target_agent,
            })
            if len(self._redirects) > self.max_traces:
                self._redirects = self._redirects[-self.max_traces:]

    def _note_dead_end(
        self,
        trace_id: str,
        step: str,
        kind: str,
        reason: str,
        decision: RedirectDecision,
    ) -> None:
        with self._lock:
            self._dead_ends.append({
                "trace_id": trace_id,
                "step": step,
                "kind": kind,
                "reason": reason,
                "redirect": decision.kind,
                "detected_at": time.time(),
            })
            if len(self._dead_ends) > self.max_traces:
                self._dead_ends = self._dead_ends[-self.max_traces:]

    # ------------------------------------------------------------------ #
    # 相似度（确定性，CJK 友好）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _similar(a: str, b: str, threshold: float) -> bool:
        """内容相似度是否达到阈值（字符 2-gram 包含率）。

        用「小集合被大集合覆盖的比例」而非对称 Jaccard，可容忍
        「同一结果 + 额外废话」的场景（死胡同的核心信号）。
        """
        ta = _ngrams(a)
        tb = _ngrams(b)
        if not ta or not tb:
            return a == b  # 两者都空/不可分词时，仅完全相等视为相似
        inter = len(ta & tb)
        return inter / min(len(ta), len(tb)) >= threshold

    # ------------------------------------------------------------------ #
    # 快照（TUI /monitor + CLI /monitor 数据源）
    # ------------------------------------------------------------------ #
    def snapshot(self, *, trace_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            traces: list[dict[str, Any]] = []
            for tid, tr in self._traces.items():
                if trace_id is not None and tid != trace_id:
                    continue
                steps = tr["steps"]
                traces.append({
                    "trace_id": tid,
                    "role": tr["role"],
                    "steps": len(steps),
                    "active": tr["status"] == "active",
                    "status": tr["status"],
                    "elapsed": round(time.time() - tr["started_at"], 1),
                    "last_step": steps[-1].step if steps else None,
                    "last_ok": steps[-1].success if steps else None,
                })
            return {
                "traces": traces,
                "dead_ends": list(self._dead_ends),
                "redirects": list(self._redirects),
                "stats": {
                    "traces": len(self._traces),
                    "active": sum(
                        1 for t in self._traces.values() if t["status"] == "active"
                    ),
                    "dead_ends": len(self._dead_ends),
                    "redirects": len(self._redirects),
                },
            }

    def hud_summary(self) -> dict[str, Any]:
        """生成 HUD 专用的精简监控快照（高维统计 + 最近事件）。

        与 snapshot() 的区别：只带 HUD 面板展示需要的最小字段，
        避免把完整轨迹/正文全部塞进 TCP 命令里。
        """
        with self._lock:
            active = sum(
                1 for t in self._traces.values() if t["status"] == "active"
            )
            top = [
                {
                    "trace_id": tid,
                    "role": tr["role"],
                    "steps": len(tr["steps"]),
                    "active": tr["status"] == "active",
                    "elapsed": round(time.time() - tr["started_at"], 1),
                    "last_step": tr["steps"][-1].step if tr["steps"] else None,
                    "last_ok": tr["steps"][-1].success if tr["steps"] else None,
                }
                for tid, tr in list(self._traces.items())[-3:]
            ]
            latest_dead = self._dead_ends[-1] if self._dead_ends else None
        return {
            "stats": {
                "traces": len(self._traces),
                "active": active,
                "dead_ends": len(self._dead_ends),
                "redirects": len(self._redirects),
            },
            "top_traces": top,
            "latest_dead": latest_dead,
        }

    def _trim_finished(self) -> None:
        """超出上限时丢弃最早的已完成轨迹（活跃轨迹不丢）。"""
        over = len(self._traces) - self.max_traces
        if over <= 0:
            return
        finished = sorted(
            (tid for tid, t in self._traces.items() if t["status"] != "active"),
            key=lambda tid: self._traces[tid]["started_at"],
        )
        for tid in finished[:over]:
            self._traces.pop(tid, None)
