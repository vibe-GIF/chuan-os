"""MCP 适配器 —— langchain-mcp-adapters 封装。

负责加载 mcp_servers.yaml 注册表，启动 MCP server 子进程（stdio），
并将其工具接口注入 LangChain 工具注册表。

用法:
    adapter = MCPAdapter()
    await adapter.connect_all()      # 启动所有 server
    tools = adapter.get_tools()      # 获取全部 LangChain Tool
    await adapter.disconnect_all()   # 关闭所有连接
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# MCP 请求超时：默认 60s 对编码类工具（如 opencode）太短
_MCP_TIMEOUT = timedelta(seconds=620)


def _wrap_mcp_tool(original: BaseTool) -> BaseTool:
    """把 MCP 的 StructuredTool 包装成普通结构化工具，兼容 glm-4-flash。

    原因一：langchain-mcp-adapters 加载的工具是 StructuredTool，其 args_schema
    为 dict 格式，智谱 glm-4-flash 的 bind_tools 不识别，导致 LLM 不生成
    tool_calls 而是把工具调用写成纯文本或直接编造。
    这里用 exec 动态生成有明确参数签名的函数，再用 StructuredTool.from_function
    包装——从函数签名生成 pydantic model 的 args_schema（与普通 @tool 一致，
    多参数工具如 filesystem 的 write_file(path, content) 也能正确绑定），
    模型能正确识别并生成 tool_calls。

    原因二：MCP StructuredTool 只支持异步调用（sync invoke 会抛
    NotImplementedError）。这里同时生成 async coroutine 传给
    StructuredTool.from_function，agent 的 ToolNode 走 ainvoke 路径时在
    MCP session 所在的事件循环里执行，真正打通同步 runtime → 异步 MCP 的链路。
    """
    name = original.name
    description = original.description or name

    # 从原始 args_schema 提取参数名
    if isinstance(original.args_schema, dict):
        props = original.args_schema.get("properties", {})
    else:
        props = {k: {} for k in original.args_schema.model_fields}

    param_names = list(props.keys())
    if not param_names:
        param_names = ["query"]

    # 动态生成有明确参数的函数（同步 + 异步两份）
    args_def = ", ".join(f"{p}: str = ''" for p in param_names)
    args_dict = "{" + ", ".join(f"'{p}': {p}" for p in param_names) + "}"
    code = f"""
def {name}({args_def}):
    return _flatten(_sync_call({args_dict}))

async def {name}_async({args_def}):
    return _flatten(await original.ainvoke({args_dict}))
"""

    def _flatten(result: Any) -> Any:
        """把 MCP 返回的 content block 列表扁平化为纯文本。

        ainvoke 返回形如 [{'type': 'text', 'text': '...'}] 的列表，
        直接给 ToolMessage 会变成 repr 字符串，干扰 LLM 阅读。
        """
        if isinstance(result, list):
            parts: list[str] = []
            for item in result:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if text:
                        parts.append(str(text))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts) if parts else result
        return result

    def _sync_call(payload: dict[str, Any]) -> Any:
        """同步兜底：MCP StructuredTool 不支持 sync invoke，退回异步。"""
        import asyncio

        try:
            return asyncio.run(original.ainvoke(payload))
        except RuntimeError:
            # 已在事件循环线程内被同步调用（理论上不会发生），明确报错
            return "[TOOL ERROR] 该工具需要在异步上下文中调用"

    namespace: dict[str, Any] = {
        "original": original,
        "_sync_call": _sync_call,
        "_flatten": _flatten,
    }
    exec(code, namespace)  # noqa: S102 - 动态生成工具函数，参数来自 MCP schema
    func = namespace[name]
    func.__doc__ = description

    # 用 StructuredTool.from_function 而非裸 Tool：裸 Tool 是单输入工具
    # （args 只有 tool_input），双参 MCP 工具（如 filesystem 的 write_file）
    # 会被判「Too many arguments to single-input tool」而炸。
    # from_function 从函数签名生成多字段 pydantic args_schema（与普通 @tool
    # 一致），glm-4-flash 可正常 bind，且保留 coroutine 走 MCP 异步链路。
    return StructuredTool.from_function(
        name=name,
        description=description,
        func=func,
        coroutine=namespace[f"{name}_async"],
    )


class MCPAdapter:
    """MCP 适配器 —— 管理所有已配置 MCP server 的连接和工具暴露。

    生命周期（需在 async 环境中使用）:
        adapter = MCPAdapter()
        await adapter.connect_all()
        ... 使用 tools ...
        await adapter.disconnect_all()
    """

    def __init__(self, config_path: str | Path = "config/mcp_servers.yaml") -> None:
        self._config: dict[str, Any] = self._load_config(config_path)
        # 每个 server 独立一个 AsyncExitStack，支持单个启停（MCP 管理面板）
        self._stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, list[BaseTool]] = {}
        self._errors: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 配置加载
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_config(path: str | Path) -> dict[str, Any]:
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent / p
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _resolve_args(args: list[str]) -> list[str]:
        """把 args 中相对项目根目录存在的脚本路径转为绝对路径。

        否则当 chuan 从其他目录启动时，``mcp_servers/xxx.py`` 这类
        相对路径会因 cwd 不同而启动失败。
        """
        if not args:
            return []
        project_root = Path(__file__).resolve().parent.parent.parent
        first = Path(args[0])
        if not first.is_absolute():
            absolute = project_root / first
            if absolute.exists():
                return [str(absolute), *args[1:]]
        return list(args)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def connect_all(self) -> None:
        """启动 mcp_servers.yaml 中注册的所有 server。

        每个 server 独立启动；单个失败不会阻断其他 server。
        失败信息记录在 self._errors。
        """
        for name in self._config.get("servers", {}):
            await self.connect_one(name)

    async def connect_one(self, name: str) -> bool:
        """连接单个 MCP server（幂等：已连接直接返回 True）。

        Args:
            name: mcp_servers.yaml 里注册的 server 名

        Returns:
            是否连接成功；失败原因写入 self._errors[name]
        """
        cfg = (self._config.get("servers") or {}).get(name)
        if cfg is None:
            self._errors[name] = "server 未在 mcp_servers.yaml 中配置"
            return False
        if name in self._stacks:
            return True  # 已连接
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=cfg.get("command", "python"),
                args=self._resolve_args(cfg.get("args", [])),
                env=cfg.get("env"),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=_MCP_TIMEOUT)
            )
            await session.initialize()
            tools = await load_mcp_tools(session)
            # 包装成普通 Tool，兼容 glm-4-flash（StructuredTool 不被识别）
            self._sessions[name] = session
            self._stacks[name] = stack
            self._tools[name] = [_wrap_mcp_tool(t) for t in tools]
            self._errors.pop(name, None)
            return True
        except Exception as exc:  # noqa: BLE001 - one MCP server must not block others
            await stack.aclose()  # 清理半开连接
            self._sessions.pop(name, None)
            self._stacks.pop(name, None)
            self._tools[name] = []
            self._errors[name] = str(exc)
            return False

    async def disconnect_one(self, name: str) -> None:
        """断开单个 MCP server 并清理其工具。"""
        stack = self._stacks.pop(name, None)
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001 - 断开失败尽力而为
                pass
        self._sessions.pop(name, None)
        self._tools.pop(name, None)
        self._errors.pop(name, None)

    async def reconnect_one(self, name: str) -> bool:
        """先断开再重连单个 MCP server。"""
        await self.disconnect_one(name)
        return await self.connect_one(name)

    async def disconnect_all(self) -> None:
        """关闭所有已配置 MCP server 的连接，清理资源与错误记录。"""
        for name in self._config.get("servers", {}):
            await self.disconnect_one(name)

    # ------------------------------------------------------------------ #
    # 工具查询
    # ------------------------------------------------------------------ #
    def get_tools(self, server_name: str | None = None) -> list[BaseTool]:
        """获取 LangChain Tool 列表。

        Args:
            server_name: 指定 server 名；None 返回所有已连接 server 的工具。
        """
        if server_name is not None:
            return list(self._tools.get(server_name, []))
        all_tools: list[BaseTool] = []
        for tools in self._tools.values():
            all_tools.extend(tools)
        return all_tools

    def list_servers(self) -> list[str]:
        """返回已配置（或尝试连接过）的 server 名称列表。"""
        return list(self._config.get("servers", {}).keys())

    def connected_servers(self) -> list[str]:
        """返回当前已成功连接的 server 名称列表。"""
        return [name for name in self._stacks if name in self._sessions]

    def connection_errors(self) -> dict[str, str]:
        """返回连接失败的 server 及其错误信息。"""
        return dict(self._errors)

    def server_status(self) -> list[dict[str, Any]]:
        """MCP 管理面板数据源：每个已配置 server 的连接/工具/错误状态。

        每项字段: name / configured / connected / tools / command /
        args / description / error。
        """
        servers: dict[str, dict[str, Any]] = self._config.get("servers", {})
        out: list[dict[str, Any]] = []
        for name, cfg in servers.items():
            out.append({
                "name": name,
                "configured": True,
                "connected": name in self._stacks and name in self._sessions,
                "tools": len(self._tools.get(name, [])),
                "command": cfg.get("command", "python"),
                "args": cfg.get("args", []),
                "description": cfg.get("description", ""),
                "error": self._errors.get(name, ""),
            })
        return out
