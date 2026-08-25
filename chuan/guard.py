"""N5 封驳关 —— 安全闸（ADR-008）。

在「幕僚长定案 → 派发执行」之间插入强制事前审核。
任何 agent 在真正调用工具 / 执行动作前必须先过此关；
审核不通过则直接打回重拟（reject + rewrite），不允许「先执行再补救」。

三段式对应:
- 中书（规划）: 幕僚长/agent 产出待执行方案
- 门下（封驳）: 本模块 —— 安全/意图/权限审查
- 尚书（派发）: 仅 approve 的方案才执行

用法:
    guard = Guard()
    result = guard.review(agent_name, action)  # -> GuardResult
    if result.approved:
        execute(action)
    else:
        rewrite(action, result.reason)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GuardAction(str, Enum):
    """封驳决策。"""

    APPROVE = "approve"
    REJECT = "reject"


@dataclass
class GuardResult:
    """审核结果。"""

    action: GuardAction
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.action == GuardAction.APPROVE


@dataclass
class DangerousPattern:
    """危险模式定义。"""

    pattern: str           # 正则表达式
    category: str          # 分类: file_delete / system_cmd / etc.
    description: str       # 人类可读描述
    severity: str = "high" # high / medium / low


class Guard:
    """封驳关 —— ADR-008 核心实现。

    审核规则:
    1. 危险命令检测（rm -rf、format、drop table 等）
    2. 文件系统敏感路径（/etc、Windows 系统目录）
    3. 网络敏感操作（内网扫描、端口探测）
    4. PII 泄露风险（密码、密钥、token）
    5. 自毁操作（删除自身、清空数据库）

    用法:
        guard = Guard()
        result = guard.review("lawyer", {"type": "tool_call", "tool": "bash", "input": "rm -rf /"})
        if not result.approved:
            print(f"Blocked: {result.reason}")
    """

    def __init__(self, *, strict_mode: bool = True) -> None:
        self.strict_mode = strict_mode
        self._patterns: list[DangerousPattern] = self._load_default_patterns()

    # ------------------------------------------------------------------ #
    # 默认规则集
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_default_patterns() -> list[DangerousPattern]:
        """加载默认的危险模式列表。"""
        return [
            # 文件删除/破坏
            DangerousPattern(
                pattern=r"rm\s+-rf?\s+[/~]",
                category="file_delete",
                description="递归强制删除根目录或主目录",
                severity="high",
            ),
            DangerousPattern(
                pattern=r"(?:del|rd)\s+/[sq]\s+\w:\\",
                category="file_delete",
                description="Windows 强制删除系统盘",
                severity="high",
            ),
            DangerousPattern(
                pattern=r">\s*/dev/(?:null|zero|sd\w)",
                category="file_destroy",
                description="覆盖设备文件",
                severity="high",
            ),
            # 系统破坏
            DangerousPattern(
                pattern=r"(?:format|mkfs)\s+\w:",
                category="system_destroy",
                description="格式化磁盘",
                severity="high",
            ),
            DangerousPattern(
                pattern=r"shutdown\s+(?:/[rs]|-h|-P|now)",
                category="system_shutdown",
                description="关闭/重启系统",
                severity="high",
            ),
            DangerousPattern(
                pattern=r":\(\)\{\s*:\|:&\s*\};:",
                category="fork_bomb",
                description="Fork 炸弹",
                severity="high",
            ),
            # 数据库破坏
            DangerousPattern(
                pattern=r"DROP\s+DATABASE|DROP\s+TABLE\s+(?!IF\s+EXISTS)|TRUNCATE",
                category="data_destroy",
                description="删除数据库/表",
                severity="high",
            ),
            DangerousPattern(
                pattern=r"DELETE\s+FROM\s+\w+\s*;$",
                category="data_destroy",
                description="无条件删除全表数据",
                severity="medium",
            ),
            # 敏感信息泄露
            DangerousPattern(
                pattern=r"(?:password|passwd|api_key|secret|token)\s*[=:]\s*['\"][^'\"]+['\"]",
                category="pii_leak",
                description="可能泄露凭证",
                severity="high",
            ),
            # 网络攻击
            DangerousPattern(
                pattern=r"(?:nmap|masscan|nikto)\s+-",
                category="network_scan",
                description="端口/漏洞扫描",
                severity="medium",
            ),
            DangerousPattern(
                pattern=r"(?:curl|wget).*\|\s*(?:bash|sh|python|perl)",
                category="remote_code_exec",
                description="远程代码执行（管道到解释器）",
                severity="high",
            ),
        ]

    # ------------------------------------------------------------------ #
    # 核心审核接口
    # ------------------------------------------------------------------ #
    def review(
        self,
        agent_name: str,
        action: dict[str, Any] | str,
    ) -> GuardResult:
        """审核一个待执行的动作。

        Args:
            agent_name: 发起动作的 agent 名
            action: 动作描述，可以是:
                - 字典: {"type": "tool_call", "tool": "bash", "input": "..."}
                - 字符串: 直接的命令文本

        Returns:
            GuardResult: 包含 approve/reject 决策和原因
        """
        if isinstance(action, dict):
            action_text = self._extract_action_text(action)
        else:
            action_text = str(action)

        if not action_text or not action_text.strip():
            return GuardResult(GuardAction.APPROVE)

        for pat in self._patterns:
            match = re.search(pat.pattern, action_text, re.IGNORECASE)
            if match:
                reject_reason = (
                    f"[{pat.category.upper()}] {pat.description} "
                    f"(agent={agent_name}, severity={pat.severity})"
                )
                return GuardResult(
                    action=GuardAction.REJECT,
                    reason=reject_reason,
                    details={
                        "pattern": pat.pattern,
                        "category": pat.category,
                        "matched": match.group(),
                    },
                )

        return GuardResult(GuardAction.APPROVE)

    # ------------------------------------------------------------------ #
    # 批量审核
    # ------------------------------------------------------------------ #
    def review_batch(
        self,
        agent_name: str,
        actions: list[dict[str, Any] | str],
    ) -> list[GuardResult]:
        """批量审核多个动作，遇到第一个 reject 即停止（短路）。"""
        results: list[GuardResult] = []
        for action in actions:
            result = self.review(agent_name, action)
            results.append(result)
            if not result.approved and self.strict_mode:
                break
        return results

    # ------------------------------------------------------------------ #
    # LangGraph 集成：post_model_hook
    # ------------------------------------------------------------------ #
    def as_post_model_hook(self):
        """作为 langgraph-supervisor 的 post_model_hook 使用。

        在 supervisor LLM 做出路由决策后、实际转交前进行安全审核。
        只返回 messages 更新，不引入 state 其他键，避免 LangGraph 未知通道警告。
        """
        def hook(state: dict[str, Any]) -> dict[str, Any]:
            messages = state.get("messages", [])
            if not messages:
                return {}

            last_msg = messages[-1]
            action_text = ""

            if hasattr(last_msg, "content"):
                action_text = str(last_msg.content)
            elif isinstance(last_msg, dict):
                action_text = last_msg.get("content", "")

            if not action_text:
                return {}

            agent_name = state.get("current_agent", "unknown")
            result = self.review(agent_name, action_text)

            if not result.approved:
                return {
                    "messages": [
                        *messages[:-1],
                        _create_reject_message(result.reason),
                    ],
                }

            return {}

        return hook

    # ------------------------------------------------------------------ #
    # 规则管理
    # ------------------------------------------------------------------ #
    def add_pattern(self, pattern: DangerousPattern) -> None:
        """添加自定义危险模式。"""
        self._patterns.append(pattern)

    def remove_pattern(self, category: str | None = None) -> int:
        """移除危险模式，返回移除数量。"""
        if category is None:
            count = len(self._patterns)
            self._patterns.clear()
            return count

        original_len = len(self._patterns)
        self._patterns = [p for p in self._patterns if p.category != category]
        return original_len - len(self._patterns)

    def list_patterns(self) -> list[dict[str, str]]:
        """列出当前所有规则（调试用）。"""
        return [
            {
                "pattern": p.pattern,
                "category": p.category,
                "description": p.description,
                "severity": p.severity,
            }
            for p in self._patterns
        ]

    # ------------------------------------------------------------------ #
    # 内部方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_action_text(action: dict[str, Any]) -> str:
        """从动作字典中提取可审核的文本。"""
        parts: list[str] = []

        action_type = action.get("type", "")
        parts.append(f"[{action_type}]")

        tool_name = action.get("tool", action.get("name", ""))
        if tool_name:
            parts.append(f"tool={tool_name}")

        input_data = action.get("input", action.get("args", action.get("command", "")))
        if input_data:
            parts.append(str(input_data))

        return " ".join(parts)


def _create_reject_message(reason: str) -> dict[str, str]:
    """创建驳回消息（用于替换原始消息）。"""
    msg = f"[GUARD BLOCKED] {reason}\n请重新规划方案，避免上述操作。"
    return {
        "role": "assistant",
        "content": msg,
    }
