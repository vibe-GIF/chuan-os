"""N43 语义嵌入客户端 —— OpenAI 兼容 embeddings API（zhipu / bailian / ollama 皆可）。

语义检索（sqlite-vec，N43）的嵌入源：默认走云端 OpenAI 兼容 embeddings
（便宜且稳），也可注入本地/自定义 provider。key 解析对齐 ``brains._resolve_api_key``：
先读环境变量，再回退 ``config/secrets.yaml`` 的字段。任何失败返回 None/空，
由 Memory 语义层静默降级回纯词法 FTS5（旁路增强，真相不动）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_secrets(path: str | Path = "config/secrets.yaml") -> dict[str, Any]:
    """读取 secrets.yaml；缺失/损坏返回空 dict。"""
    p = Path(path)
    if not p.is_absolute():
        p = _project_root() / p
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_api_key(
    env_var: str | None,
    fallback: str | None,
    secrets: dict[str, Any] | None = None,
) -> str | None:
    """优先读环境变量，其次 secrets.yaml 的 fallback 字段（对齐 brains._resolve_api_key）。

    ``secrets`` 显式传入时用其兜底（测试注入），否则懒加载 secrets.yaml。
    """
    if env_var:
        val = os.environ.get(env_var)
        if val:
            return val
    if fallback:
        secrets = secrets if secrets is not None else _load_secrets()
        val = secrets.get(fallback)
        if val:
            return str(val)
    return None


class EmbeddingClient:
    """OpenAI 兼容 embeddings 客户端（批量 embed，向量长度固定 ``dim``）。"""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        dim: int,
        timeout: float = 15.0,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.dim = int(dim)

    @classmethod
    def from_config(
        cls,
        cfg: dict[str, Any],
        *,
        secrets: dict[str, Any] | None = None,
    ) -> "EmbeddingClient | None":
        """按 config 的 ``memory.semantic`` 段构建；缺 model/base_url/dim/key 返回 None。

        ``secrets`` 供测试注入；缺省从 secrets.yaml 兜底解析 key。
        """
        model = str(cfg.get("model") or "").strip()
        base_url = str(cfg.get("base_url") or "").strip()
        try:
            dim = int(cfg.get("dim") or 0)
        except (TypeError, ValueError):
            dim = 0
        if not model or not base_url or dim <= 0:
            return None
        api_key = resolve_api_key(
            cfg.get("api_key_env"), cfg.get("api_key_secret"), secrets
        )
        if not api_key:
            return None
        return cls(model=model, base_url=base_url, api_key=api_key, dim=dim)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入；结果按输入顺序返回，每条长度为 ``dim``。空输入返回空。"""
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, input=texts)
        data = sorted(resp.data, key=lambda d: d.index)
        return [list(d.embedding) for d in data]

    def embed_one(self, text: str) -> list[float]:
        """单条嵌入；失败/空返回空列表。"""
        vecs = self.embed([text])
        return vecs[0] if vecs else []
