"""N43 语义嵌入客户端（chuan/embed.py）单元测试。

不触网：key 解析纯本地；embed 排序用 mock 掉 OpenAI 客户端。
"""

from __future__ import annotations

from types import SimpleNamespace

from chuan.embed import EmbeddingClient, resolve_api_key


def test_resolve_api_key_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("MY_KEY", "envval")
    assert resolve_api_key("MY_KEY", "fallback", {"fallback": "secval"}) == "envval"


def test_resolve_api_key_falls_back_to_secrets() -> None:
    assert resolve_api_key("NOPE", "fallback", {"fallback": "secval"}) == "secval"


def test_resolve_api_key_missing() -> None:
    assert resolve_api_key("NOPE", "missing_field", {}) is None
    assert resolve_api_key(None, None, {}) is None


def test_embedding_client_from_config_requires_key(monkeypatch) -> None:
    cfg = {
        "model": "text-embedding-v3",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "dim": 1024,
        "api_key_env": "NO_SUCH_ENV",
        "api_key_secret": "no_such_secret",
    }
    # 无 key 可解析 → None（语义层据此关闭，降级词法）
    assert EmbeddingClient.from_config(cfg, secrets={}) is None

    monkeypatch.setenv("MY_EMBED_KEY", "k")
    cfg["api_key_env"] = "MY_EMBED_KEY"
    client = EmbeddingClient.from_config(cfg, secrets={})
    assert client is not None
    assert client.model == "text-embedding-v3"
    assert client.dim == 1024


def test_embedding_client_from_config_rejects_bad_dim() -> None:
    cfg = {"model": "m", "base_url": "http://x", "dim": 0, "api_key_env": "MY_EMBED_KEY"}
    assert EmbeddingClient.from_config(cfg, secrets={"MY_EMBED_KEY": "k"}) is None


def test_embed_sorts_by_index() -> None:
    client = EmbeddingClient(model="m", base_url="http://x", api_key="k", dim=2)

    class FakeResp:
        def __init__(self) -> None:
            # 故意乱序（index=1 在前），验证按 index 排序
            self.data = [
                SimpleNamespace(index=1, embedding=[0, 1]),
                SimpleNamespace(index=0, embedding=[1, 0]),
            ]

    def fake_create(model, input):
        assert model == "m" and input == ["a", "b"]
        return FakeResp()

    client._client.embeddings.create = fake_create  # type: ignore[method-assign]
    assert client.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert client.embed([]) == []
