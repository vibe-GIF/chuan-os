"""MCP 管理面板：MCPAdapter 单 server 启停 + 状态查询测试。

不依赖真实 MCP server：失败路径用不存在的命令快速触发；
已配置状态的字段校验用临时 yaml。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from langchain_core.tools import tool

from chuan.adapters.mcp_adapter import MCPAdapter, _wrap_mcp_tool


def _write_config(tmp_path: Path, servers: dict) -> Path:
    p = tmp_path / "mcp_servers.yaml"
    p.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    return p


def _run(coro):
    return asyncio.run(coro)


def test_server_status_reflects_config(tmp_path: Path) -> None:
    """server_status() 正确回读 yaml 配置字段，未连接时 connected=False。"""
    cfg = _write_config(tmp_path, {
        "filesystem": {
            "command": "python",
            "args": ["mcp_servers/filesystem_server.py"],
            "description": "文件系统 MCP",
        },
    })
    adapter = MCPAdapter(cfg)
    status = adapter.server_status()
    assert len(status) == 1
    s = status[0]
    assert s["name"] == "filesystem"
    assert s["configured"] is True
    assert s["connected"] is False
    assert s["tools"] == 0
    assert s["command"] == "python"
    assert s["args"] == ["mcp_servers/filesystem_server.py"]
    assert s["description"] == "文件系统 MCP"
    assert s["error"] == ""


def test_connect_one_unknown_server_records_error(tmp_path: Path) -> None:
    """connect_one 未配置的 server → 返回 False 且记录错误，不炸。"""
    cfg = _write_config(tmp_path, {"real": {"command": "python", "args": []}})
    adapter = MCPAdapter(cfg)
    ok = _run(adapter.connect_one("ghost"))
    assert ok is False
    assert "ghost" in adapter.connection_errors()
    assert adapter.connected_servers() == []


def test_disconnect_one_unknown_is_noop(tmp_path: Path) -> None:
    """disconnect_one 未连接的 server → 静默无操作。"""
    cfg = _write_config(tmp_path, {"real": {"command": "python", "args": []}})
    adapter = MCPAdapter(cfg)
    _run(adapter.disconnect_one("ghost"))  # 不抛即可
    assert adapter.connected_servers() == []


def test_connect_all_failure_isolated_and_reported(tmp_path: Path) -> None:
    """两个 server 都启动失败时互不阻断，且状态面板能看到错误。"""
    cfg = _write_config(tmp_path, {
        "a": {"command": "definitely-not-a-real-cmd-xyz", "args": []},
        "b": {"command": "definitely-not-a-real-cmd-xyz", "args": []},
    })
    adapter = MCPAdapter(cfg)
    _run(adapter.connect_all())
    assert adapter.connected_servers() == []
    assert set(adapter.connection_errors()) == {"a", "b"}
    status = adapter.server_status()
    assert all(not s["connected"] for s in status)
    assert all(s["error"] for s in status)


def test_disconnect_all_clears_state(tmp_path: Path) -> None:
    """disconnect_all 后 server_status 全部 connected=False、无错误。"""
    cfg = _write_config(tmp_path, {
        "a": {"command": "definitely-not-a-real-cmd-xyz", "args": []},
    })
    adapter = MCPAdapter(cfg)
    _run(adapter.connect_all())
    assert set(adapter.connection_errors()) == {"a"}
    _run(adapter.disconnect_all())
    assert adapter.connected_servers() == []
    assert adapter.connection_errors() == {}
    assert all(not s["connected"] for s in adapter.server_status())


# ── _wrap_mcp_tool：多参数 MCP 工具包装（write_file 回归） ──


def test_wrap_mcp_tool_supports_multi_arg_tool() -> None:
    """双参 MCP 工具（write_file(path, content)）包装后必须能多输入调用。

    回归：裸 Tool 是单输入工具，双参工具会被判「Too many arguments to
    single-input tool」而炸；改用 StructuredTool.from_function 后
    args 是多字段 schema，ainvoke 传 dict 正常执行。
    """

    @tool
    def write_file(path: str, content: str) -> str:
        """写入文件。"""
        return f"wrote {path}: {content}"

    wrapped = _wrap_mcp_tool(write_file)
    assert list(wrapped.args) == ["path", "content"]  # 多输入，而非 tool_input
    out = _run(wrapped.ainvoke({"path": "a.txt", "content": "hi"}))
    assert out == "wrote a.txt: hi"


def test_wrap_mcp_tool_single_arg_still_works() -> None:
    """单参 MCP 工具（read_file）包装后行为不变。"""

    @tool
    def read_file(path: str) -> str:
        """读取文件。"""
        return f"read {path}"

    wrapped = _wrap_mcp_tool(read_file)
    assert list(wrapped.args) == ["path"]
    assert _run(wrapped.ainvoke({"path": "README.md"})) == "read README.md"
