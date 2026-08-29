"""把长期记忆（Memory）包装成 agent 可调用的 LangChain 工具（N13 三层闭环）。

agent 在工作流中通过 `remember_memory` 沉淀经验、用 `recall_memory` 召回
历史结论，从而让记忆真正参与决策，而不是停留在底层仓库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool, Tool

_MEMORY_FILE = "MEMORY.md"


def build_role_memory_tools(directory: Path) -> list[Tool]:
    """为目录格式角色（ADR-013）生成读写其私有 ``MEMORY.md`` 的工具。

    MEMORY.md 是角色的私有记忆，位于 ``personas/<name>/MEMORY.md``，
    与共享黑板（Obsidian）和长期记忆库（notes/）区分。
    """
    memory_file = directory / _MEMORY_FILE

    def read_role_memory() -> str:
        """读取本角色的私有记忆 MEMORY.md 全文；为空则提示。"""
        if not memory_file.exists():
            return "（暂无私有记忆）"
        try:
            text = memory_file.read_text(encoding="utf-8")
        except OSError:
            return "（读取失败）"
        return text or "（暂无私有记忆）"

    def append_role_memory(note: str) -> str:
        """向本角色的私有记忆 MEMORY.md 追加一行笔记。用于沉淀经验、决策、教训，
        下次会话可读回，实现角色自改进。"""
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        with memory_file.open("a", encoding="utf-8") as f:
            f.write(note.rstrip() + "\n")
        return f"已记录到 {memory_file}"

    return [
        Tool(
            name="read_role_memory",
            description="读取本角色私有记忆 MEMORY.md 的全文（做过什么、偏好、教训）。",
            func=read_role_memory,
        ),
        Tool(
            name="append_role_memory",
            description=(
                "向本角色私有记忆 MEMORY.md 追加一行笔记。"
                "把经验、决策依据、教训写进去，下次可读回。参数 note 为要追加的内容。"
            ),
            func=append_role_memory,
        ),
    ]


def build_memory_tools(memory: Any) -> list[Tool]:
    """基于一个 ``chuan.memory.Memory`` 实例生成回忆/记录两个工具。"""

    def remember_memory(
        name: str, content: str, importance: int = 3, tags: list | None = None,
        source: str = "", type: str = "memory",
    ) -> str:
        """记录一条长期记忆。name 为文档标题（英文/数字/下划线/点/连字符），
        content 为记忆正文。importance 为重要性（1-5，默认 3），tags 为标签列表，
        source 为来源描述。type 为记忆类型（fact 事实/preference 偏好/
        process 过程/memory 默认，默认 memory）。之后可随时用 recall_memory 召回。"""
        path = memory.remember(
            name, content, importance=importance, tags=tags, source=source, type=type
        )
        return f"已记录到 {path}"

    def recall_memory(
        query: str, limit: int = 5, min_importance: int = 0, type: str = ""
    ) -> str:
        """按查询词召回最相关的长期记忆。返回命中文档路径与片段；
        无命中返回提示。limit 控制返回条数（默认 5）。
        min_importance 为重要性下限（0-5，默认 0 不过滤），
        设为 3 可只召回中等及以上重要性的记忆。
        type 为记忆类型过滤（fact/preference/process/memory，默认不过滤）。"""
        hits = memory.recall(
            query, limit=limit, min_importance=min_importance,
            type=type or None,
        )
        if not hits:
            return "（没有找到相关记忆）"
        parts = []
        for hit in hits:
            snippet = hit.content.strip().splitlines()[0][:120]
            parts.append(f"[{hit.relative_path}] {snippet}")
        return "\n".join(parts)

    return [
        StructuredTool.from_function(
            name="remember_memory",
            description=(
                "把重要结论、经验、事实记录到长期记忆库，之后可用 recall_memory 召回。"
                "name 用简短英文/数字标识（如 deploy_notes）；content 是正文；"
                "importance 是重要性 1-5（默认 3，关键结论用 4-5）；"
                "tags 是标签列表；source 记来源；"
                "type 是记忆类型（fact 事实/preference 偏好/process 过程，默认 memory）。"
            ),
            func=remember_memory,
        ),
        StructuredTool.from_function(
            name="recall_memory",
            description=(
                "检索历史长期记忆。做过的决定、写过的结论、查过的资料都能回来看。"
                "参数：query 检索词，limit 返回条数，"
                "min_importance 重要性下限（默认 0 不过滤，3 只召回中等及以上）；"
                "type 记忆类型过滤（fact/preference/process/memory，默认不过滤）。"
            ),
            func=recall_memory,
        ),
    ]


def build_wiki_tools(memory: Any) -> list[Tool]:
    """基于 ``chuan.memory.Memory`` 生成 Wiki 知识库维护工具（N24）。

    与 remember/recall 的区别：wiki 走「实体归并」结构——同名页合并更新而非
    追加，并维护 index.md 索引与 log.md 审计（Karpathy LLM Wiki 模式）。
    """

    def wiki_write(
        entity_type: str,
        name: str,
        content: str,
        importance: int = 3,
        confidence: int = 3,
        tags: list | None = None,
        source: str = "",
    ) -> str:
        """写入/更新一个 wiki 实体页。entity_type ∈ topics/entities/analysis/projects
        （sources 只读，原料走导入）。同名实体页会合并更新而非新建：保留创建时间、
        新内容追加为同名小节、旧声明折叠为 deprecated。importance/confidence 1-5。"""
        from chuan.wiki import Wiki

        wiki = Wiki(memory)
        path = wiki.write(
            entity_type,
            name,
            content,
            importance=importance,
            confidence=confidence,
            tags=tags,
            source=source,
        )
        return f"已写入 {path}"

    def wiki_search(query: str, limit: int = 10) -> str:
        """先在 wiki 索引（index.md）里按关键词定位页面，再回退全文召回。
        返回命中的 [[wikilink]] 页面列表；无命中返回提示。"""
        from chuan.wiki import Wiki

        wiki = Wiki(memory)
        hits = wiki.search_index(query, limit=limit)
        if hits:
            parts = [f"[{h['rel_path']}] {h['snippet']}" for h in hits]
            return "\n".join(parts)
        # 回退全文召回（含 sources/ 原料）
        memory_hits = memory.recall(query, limit=limit)
        if not memory_hits:
            return "（wiki 索引与全文均无命中）"
        return "\n".join(
            f"[{h.relative_path}] {h.content.strip().splitlines()[0][:120]}"
            for h in memory_hits
        )

    return [
        StructuredTool.from_function(
            name="wiki_write",
            description=(
                "把知识写入结构化 wiki 知识库（实体归并，非追加）。"
                "entity_type 五选一：topics(主题)/entities(实体)/analysis(分析结论)"
                "/projects(项目)；name 是实体名（同一实体多次写入会合并更新同一页，"
                "新声明覆盖旧声明并标注 deprecated，不会重复建页）。"
                "importance/confidence 1-5（默认 3）。tags 标签列表，source 记来源。"
                "适合沉淀：主题积累、人物/项目画像、分析结论。"
            ),
            func=wiki_write,
        ),
        StructuredTool.from_function(
            name="wiki_search",
            description=(
                "检索 wiki 知识库。先在 index.md 索引里按关键词定位页面（无需向量检索），"
                "无索引命中时回退全文召回。返回相关 [[wikilink]] 页面列表。"
                "参数：query 检索词，limit 返回条数。"
            ),
            func=wiki_search,
        ),
    ]


def build_howto_tools(memory: Any) -> list[Tool]:
    """基于 chuan.memory.Memory 生成 L3「从做到造」知识原子工具（N26）。

    沉淀：agent 识别到「可复用的怎么做过程」时 howto_save（同名更新）。
    复用：开工前 howto_find 自查 / Department 自动注入 suggest。
    """
    from chuan.howto import HowToStore

    store = HowToStore(memory)

    def howto_save(
        name: str,
        trigger: str,
        process: str,
        tools: str = "",
        importance: int = 3,
    ) -> str:
        """沉淀一个可复用的「怎么做」知识原子（同名更新）。"""
        path = store.save(
            name, trigger, process, tools=tools, importance=importance,
            source="agent:howto_save",
        )
        return f"已沉淀知识原子 {path}"

    def howto_find(query: str, limit: int = 5) -> str:
        """按触发场景/内容检索已有知识原子（复用「怎么做」）。"""
        hits = store.find(query, limit=limit)
        if not hits:
            return "（没有匹配的已有做法，可以从零做并沉淀）"
        return "\n".join(
            f"[{h['name']}] score={h['score']:.1f} 触发：{h['trigger'] or '无'} | "
            f"{h['content'].strip().splitlines()[0][:100]}"
            for h in hits
        )

    def howto_show(name: str) -> str:
        """读取一个知识原子的完整做法。"""
        text = store.get(name)
        return text if text is not None else f"（未找到知识原子：{name}）"

    return [
        StructuredTool.from_function(
            name="howto_save",
            description=(
                "沉淀一个可复用的「怎么做」知识原子（L3 从做到造）。"
                "当你发现某个任务有可复用的过程（步骤/经验/坑）时调用："
                "name 原子名（同名更新不重复建页）；trigger 触发场景关键词"
                "（何时该复用这个做法）；process 怎么做（步骤、输入输出、"
                "经验坑）；tools 涉及的 agent/工具（逗号分隔）。"
            ),
            func=howto_save,
        ),
        StructuredTool.from_function(
            name="howto_find",
            description=(
                "检索已有知识原子（怎么做）。开工前调用可复用既往做法，"
                "避免从零开始。参数 query 触发场景/内容关键词，limit 条数。"
            ),
            func=howto_find,
        ),
        Tool(
            name="howto_show",
            description="读取一个知识原子的完整「怎么做」内容。参数 name 为原子名。",
            func=howto_show,
        ),
    ]


def build_vault_tools(memory: Any) -> list[Tool]:
    """基于 chuan.memory.Memory 生成外接知识库检索工具（N36）。

    与内部记忆管道隔离：``search_vault`` 只查 config ``memory.external_vaults``
    里显式配置的外接 Obsidian 库（只读，独立 vault key，绝不写回），
    不混入默认 ``recall_memory`` 的内部记忆结果——「临时查外置库」专用。
    """
    def list_vaults() -> str:
        """列出当前配置的外接知识库（名称 + 路径）。无配置返回提示。"""
        try:
            vaults = memory._load_external_vaults()
        except Exception:  # noqa: BLE001 - 配置读取失败不阻断
            return "（读取外接库配置失败）"
        if not vaults:
            return "（未配置外接知识库。config.yaml → memory.external_vaults）"
        return "\n".join(f"- {name}: {root}" for name, root in vaults)

    def search_vault(query: str, vault: str = "", limit: int = 5) -> str:
        """临时检索外接知识库（Obsidian 等，只读）。

        vault 为 config 里配置的库名；留空时检索全部已配置外接库。
        命中文档与摘要按相关度返回；无命中/未配置返回提示。
        """
        try:
            # namespaces=[] → 只查外接库，绝不混入内部 notes/（与 recall_memory 隔离）
            # vaults=[] → 空库名 = 检索全部已配置外接库
            hits = memory.recall(
                query, limit=limit, namespaces=[], vaults=[vault] if vault else []
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败返回可读错误
            return f"（检索失败：{exc}）"
        if not hits:
            return "（外接知识库没有相关命中）"
        parts = []
        for hit in hits:
            snippet = hit.content.strip().splitlines()[0][:120]
            parts.append(f"[{hit.relative_path}] {snippet}")
        return "\n".join(parts)

    return [
        Tool(
            name="list_vaults",
            description=(
                "列出已配置的外接知识库（名称 + 路径）。"
                "检索外接库前可先调用本工具确认有哪些库可用。"
            ),
            func=list_vaults,
        ),
        StructuredTool.from_function(
            name="search_vault",
            description=(
                "临时检索外接知识库（config.yaml → memory.external_vaults 配置的"
                "Obsidian 等库，只读）。与内部长期记忆隔离，不写入任何东西。"
                "参数：query 检索词；vault 库名（留空检索全部外接库）；"
                "limit 返回条数。适合查用户笔记/外部资料库里的具体内容。"
            ),
            func=search_vault,
        ),
    ]