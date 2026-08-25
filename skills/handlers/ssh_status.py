"""SSH 状态采集 handler —— 确定性查看已配置主机 / known_hosts / 活跃连接。
读取 ~/.ssh/config 与 known_hosts，Windows 用 netstat 检测 ESTABLISHED 到 22 端口。
被 skills/ssh_status.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。
任何读取失败静默降级，不抛错。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _ssh_config_hosts() -> list:
    """~/.ssh/config 里显式 Host 定义的主机别名（跳过通配符）。"""
    cfg = Path.home() / ".ssh" / "config"
    hosts: list = []
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            parts = s.split(None, 1)
            if len(parts) != 2 or parts[0].lower() != "host":
                continue
            alias = parts[1].split()[0]
            if "*" in alias or "?" in alias:
                continue
            hosts.append(alias)
    except (OSError, UnicodeDecodeError, IndexError):
        pass
    return hosts


def _ssh_known_hosts() -> list:
    """known_hosts 里出现过的主机（去重，去掉 [host]:port 与 hashed 行）。"""
    kh = Path.home() / ".ssh" / "known_hosts"
    hosts: set = set()
    try:
        for line in kh.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cols = line.split()
            if not cols:
                continue
            first = cols[0].strip("[]")
            if "," in first:
                first = first.split(",")[0]
            if first and not first.startswith("|"):
                hosts.add(first)
    except (OSError, UnicodeDecodeError, IndexError):
        pass
    return sorted(hosts)


def _active_ssh_connections() -> list:
    """Windows netstat：ESTABLISHED 且远端端口为 22 的连接。"""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    conns: list = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[0] == "TCP" and cols[3] == "ESTABLISHED":
            remote = cols[2]
            if remote.rsplit(":", 1)[-1] == "22":
                conns.append(remote)
    return conns


def ssh_status(host: str = "") -> str:
    """采集 SSH 状态：已配置主机、known_hosts、活跃连接。

    Args:
        host: 可选，查询指定主机（仅提示；连通性检测需主动连接，这里不做）。

    Returns:
        状态文本；缺失项降级为可读说明。
    """
    configured = _ssh_config_hosts()
    if configured:
        part_cfg = "已配置主机(config): " + ", ".join(configured)
    else:
        part_cfg = "已配置主机(config): 无（未找到 ~/.ssh/config）"

    known = _ssh_known_hosts()
    if known:
        show = ", ".join(known[:20])
        part_known = "known_hosts 主机: " + show + (" …" if len(known) > 20 else "")
    else:
        part_known = "known_hosts 主机: 无"

    active = _active_ssh_connections()
    if active:
        part_active = "活跃 SSH 连接: " + ", ".join(active)
    else:
        part_active = "活跃 SSH 连接: 无"

    lines = [part_cfg, part_known, part_active]
    if host:
        lines.append(f"注解: 已指定主机 '{host}'（连通性检测需主动发起，此处仅列配置与现网连接）")
    return "\n".join(lines)