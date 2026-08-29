"""③ Agent Spawner —— 角色动态出生/销毁，sub_agent 按需创建。

职责：为每个内置 persona 创建岗位（懒加载），常驻 agent 注册，以及关闭时的清理。
从 RuntimeSupervisor 迁移而来（ADR-012 Gateway 拆分）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor

from chuan.role import Department


class AgentSpawner:
    """角色岗位的出生与销毁。"""

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    def spawn(self, exclude: set[str]) -> None:
        """为每个内置 persona 创建岗位；外来 agent 不创建岗位，只留常驻池。

        幕僚长自己的岗位单独存为 _chief_role，不进可路由 workers。

        N40：从 config.yaml 的 ``role_instances`` 加载声明式实例配置，
        注入所有岗位（按任务复杂度选实例；缺段/空 → 全默认，零开销旁路）。
        """
        sup = self._sup
        skip = set(exclude)
        from chuan.role_config import load_role_instances

        instance_config = load_role_instances(sup.config_path, sup.brains)
        sup._workers = {}
        for name in sup._persona_loader.list_personas():
            if name in skip:
                continue
            persona = sup._persona_loader.get_persona(name)
            if persona is None:
                continue
            if getattr(persona, "external", False):
                continue
            try:
                sup._workers[name] = Department(
                    persona, sup._agent_pool, sup.memory.checkpointer,
                    on_progress=sup._on_progress_cb,
                    monitor=getattr(sup, "supervisor_monitor", None),
                    memory=sup.memory,
                    resume_store=getattr(sup, "resume_store", None),
                    instance_config=instance_config,
                )
            except Exception as exc:  # noqa: BLE001 - 单岗位失败不阻塞启动
                print(f"[WARNING] 岗位 '{name}' 创建失败: {exc}")

        chief_persona = sup._persona_loader.get_persona("chief_of_staff")
        if chief_persona is not None:
            sup._chief_role = Department(
                chief_persona, sup._agent_pool, sup.memory.checkpointer,
                on_progress=sup._on_progress_cb,
                monitor=getattr(sup, "supervisor_monitor", None),
                memory=sup.memory,
                resume_store=getattr(sup, "resume_store", None),
                instance_config=instance_config,
            )

        if not sup._workers:
            raise RuntimeError(
                "没有可用的 worker 岗位。请检查 personas/ 目录和配置。"
            )

    def despawn(self) -> None:
        """清理 agent 池、释放已 born 的内置 agent、清空岗位与角色总线。"""
        sup = self._sup
        try:
            sup._agent_pool.cleanup_temp()
        except Exception:  # noqa: BLE001
            pass
        for name in list(sup._persona_loader.list_born()):
            sup._persona_loader.kill(name)
        sup._workers.clear()
        from chuan import team_bus

        team_bus.clear()
        sup._chief_role = None