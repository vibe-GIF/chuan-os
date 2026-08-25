"""N36 外接知识库检索工具（search_vault）：临时查 Obsidian 库，与内部记忆隔离。"""

from __future__ import annotations

from pathlib import Path

import yaml

from chuan.memory import Memory
from chuan.memory_tools import build_vault_tools


def _vault_memory(tmp_path: Path) -> tuple[Memory, Path]:
    """构造带外接库配置的 Memory：外接库在 tmp_path/ext_vault。"""
    ext = tmp_path / "ext_vault"
    (ext / "notes").mkdir(parents=True, exist_ok=True)
    (ext / "notes" / "obsidian_note.md").write_text(
        "这份 Obsidian 笔记记录了密码重置的完整流程。", encoding="utf-8"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({
            "memory": {
                "external_vaults": [{"name": "obsidian", "path": str(ext)}]
            }
        }),
        encoding="utf-8",
    )
    memory = Memory(vault_path=tmp_path / "vault", config_path=cfg)
    # 外接库必须先 reindex 进 FTS 才能被检索（与 N25 启动挂接一致）
    memory.reindex_external()
    return memory, ext


# --------------------------------------------------------------------- #
# search_vault：外接库检索（只读、不混入内部记忆）
# --------------------------------------------------------------------- #
def test_search_vault_returns_external_hits(tmp_path: Path) -> None:
    memory, _ = _vault_memory(tmp_path)
    tools = {t.name: t for t in build_vault_tools(memory)}
    out = tools["search_vault"].func("密码重置", vault="obsidian")
    assert "obsidian_note.md" in out
    assert "密码重置" in out


def test_search_vault_empty_vault_searches_all(tmp_path: Path) -> None:
    memory, _ = _vault_memory(tmp_path)
    tools = {t.name: t for t in build_vault_tools(memory)}
    out = tools["search_vault"].func("密码重置")  # vault 留空 → 全部外接库
    assert "obsidian_note.md" in out


def test_search_vault_no_hit_returns_hint(tmp_path: Path) -> None:
    memory, _ = _vault_memory(tmp_path)
    tools = {t.name: t for t in build_vault_tools(memory)}
    out = tools["search_vault"].func("qwertyuiopasdfgh", vault="obsidian")
    assert "没有相关命中" in out


def test_search_vault_does_not_mix_internal_memory(tmp_path: Path) -> None:
    """外接库检索不混入内部记忆管道。"""
    memory, _ = _vault_memory(tmp_path)
    memory.remember("internal", "内部记忆：服务器扩容方案。")
    tools = {t.name: t for t in build_vault_tools(memory)}
    # 内部记忆只在内部管道（recall_memory），search_vault 只查外接库
    out = tools["search_vault"].func("服务器扩容", vault="obsidian")
    assert "没有相关命中" in out


def test_search_vault_unknown_vault_returns_hint(tmp_path: Path) -> None:
    memory, _ = _vault_memory(tmp_path)
    tools = {t.name: t for t in build_vault_tools(memory)}
    out = tools["search_vault"].func("密码", vault="nope")
    assert "没有相关命中" in out or "检索失败" in out


# --------------------------------------------------------------------- #
# list_vaults：列出已配置外接库
# --------------------------------------------------------------------- #
def test_list_vaults_lists_configured(tmp_path: Path) -> None:
    memory, ext = _vault_memory(tmp_path)
    tools = {t.name: t for t in build_vault_tools(memory)}
    out = tools["list_vaults"].func()
    assert "obsidian" in out
    assert str(ext) in out


def test_list_vaults_empty_when_not_configured(tmp_path: Path) -> None:
    # 显式空 config：隔离项目默认 config 里可能配置的外接库
    cfg = tmp_path / "empty_config.yaml"
    cfg.write_text(yaml.safe_dump({}), encoding="utf-8")
    memory = Memory(vault_path=tmp_path / "vault", config_path=cfg)  # 无外接库配置
    tools = {t.name: t for t in build_vault_tools(memory)}
    out = tools["list_vaults"].func()
    assert "未配置外接知识库" in out


# --------------------------------------------------------------------- #
# 工具暴露
# --------------------------------------------------------------------- #
def test_vault_tools_names_and_descriptions(tmp_path: Path) -> None:
    memory, _ = _vault_memory(tmp_path)
    tools = build_vault_tools(memory)
    names = sorted(t.name for t in tools)
    assert names == ["list_vaults", "search_vault"]
    for t in tools:
        assert t.description  # 工具描述非空，供模型理解
