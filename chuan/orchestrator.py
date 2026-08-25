"""N4 轻量路由器 —— 显式锁定 + 关键词解析。

处理简单路由逻辑，复杂的多轮/语义路由交给 RuntimeSupervisor（LLM 兜底）。

路由优先级（从高到低）:
1. 显式锁定：用户明确说"切到律师"、"找程序员"
2. 关键词计分：按 persona YAML 中的 keyword_scoring 规则匹配
3. 本体识别：LLM 判断意图（由 RuntimeSupervisor 处理）
4. 兜底：默认自己处理或指定角色

用法:
    router = Orchestrator(persona_loader)
    target = router.route("帮我看份合同")   # -> "lawyer"
"""

from __future__ import annotations

from typing import Any

from chuan.persona_loader import PersonaLoader


class Orchestrator:
    """轻量路由器 —— 基于规则的快速路由。

    用于在 LLM 路由之前做一层快速匹配，减少 LLM 调用开销。
    返回 None 表示无法确定路由目标，交给上层 LLM 处理。
    """

    def __init__(self, persona_loader: PersonaLoader | None = None) -> None:
        self._loader = persona_loader or PersonaLoader()
        self._chief_routing: dict[str, Any] = self._load_chief_routing()

    # ------------------------------------------------------------------ #
    # 核心路由
    # ------------------------------------------------------------------ #
    def route(self, message: str) -> str | None:
        """对用户消息进行路由决策。

        Args:
            message: 用户输入文本

        Returns:
            目标 persona 名；None 表示需要 LLM 兜底
        """
        if not message:
            return None

        # 1) 显式锁定（最高优先级）
        explicit = self._check_explicit_lock(message)
        if explicit:
            return explicit

        # 2) 关键词计分
        scored = self._score_keywords(message)
        if scored:
            return scored

        # 3) 无法确定，返回 None 让 LLM 兜底
        return None

    # ------------------------------------------------------------------ #
    # 路由规则
    # ------------------------------------------------------------------ #
    def _load_chief_routing(self) -> dict[str, Any]:
        """加载幕僚长的路由配置。"""
        chief = self._loader.get_persona("chief_of_staff")
        if chief:
            return chief.routing
        return {}

    def _check_explicit_lock(self, message: str) -> str | None:
        """检查显式锁定触发词。

        例如："切到律师"、"锁定 programmer" 等。
        """
        routing = self._chief_routing
        explicit_cfg = routing.get("explicit_lock", {})
        triggers: list[str] = explicit_cfg.get("trigger", [])

        msg_lower = message.lower()

        for trigger in triggers:
            if trigger.lower() in msg_lower:
                # 尝试从消息中提取目标角色名
                for worker_name in self._loader.list_personas():
                    if worker_name.lower() in msg_lower and worker_name != "chief_of_staff":
                        return worker_name

        return None

    def _score_keywords(self, message: str) -> str | None:
        """基于关键词计分选择最匹配的角色。

        返回得分最高的角色名；无匹配返回 None。
        """
        routing = self._chief_routing
        keyword_scoring: dict[str, list[str]] = routing.get("keyword_scoring", {})

        if not keyword_scoring:
            return None

        msg_lower = message.lower()
        scores: dict[str, int] = {}

        for role, keywords in keyword_scoring.items():
            score = sum(1 for kw in keywords if kw.lower() in msg_lower)
            if score > 0:
                scores[role] = score

        if not scores:
            return None

        # 返回得分最高的角色
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #
    def get_routing_config(self) -> dict[str, Any]:
        """获取当前路由配置（调试用）。"""
        return dict(self._chief_routing)

    def list_available_targets(self) -> list[str]:
        """列出所有可路由的目标 persona 名（排除幕僚长自己）。"""
        return [
            name
            for name in self._loader.list_personas()
            if name != "chief_of_staff"
        ]
