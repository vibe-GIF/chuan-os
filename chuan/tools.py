"""工具注册兼容入口（保留文件，无实际代码）。

工具注册已迁移至 ``chuan.adapters.skill_loader.SkillRegistry``：
  - 从 ``skills/*.yaml`` 加载技能定义；
  - handler 类型通过 ``importlib`` 动态导入并包装为 LangChain ``Tool``；
  - mcp 类型走 ``MCPAdapter`` 获取外部工具；
  - 最终由 ``ToolRegistry.get_tools(deny=...)`` 统一组装，支持 persona 减法挂载。

本文件不再定义任何工具或注册表，仅作为历史兼容占位保留，
避免旧代码中 ``from chuan.tools import ...`` 触发 ImportError。
新代码请直接使用 ``chuan.adapters.skill_loader``。
"""
