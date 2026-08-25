"""N1 大脑层 —— 三档模型统一接口。

负责从 config 加载 openrouter / ollama 配置，实例化为 LangChain chat model，
对外暴露统一的 `.complete()` 方法（消息列表进，字符串出）。

用法:
    registry = BrainRegistry()          # 自动读 config
    brain = registry.default()          # 取默认脑
    reply = brain.complete("你好")       # 或传消息列表

技术选型:
- openrouter → langchain_openai.ChatOpenAI（OpenRouter 兼容 OpenAI API）
- ollama     → langchain_ollama.ChatOllama

这样返回的 chat model 对象天然可喂给 LangGraph 的 create_react_agent，
不需要额外包装。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI


class Brain:
    """单个大脑的封装，对外统一接口，对内持有 LangChain chat model。"""

    def __init__(self, chat_model: BaseChatModel, name: str = "") -> None:
        self.model: BaseChatModel = chat_model
        self.name: str = name

    # ------------------------------------------------------------------ #
    # 统一完成接口
    # ------------------------------------------------------------------ #
    def complete(
        self,
        messages: str | list[dict[str, str]],
        *,
        system: str | None = None,
        **invoke_kwargs: Any,
    ) -> str:
        """发消息给模型，拿文本回复。

        Args:
            messages: 单条字符串（当作 user 消息）或消息列表，格式:
                [{"role": "user", "content": "..."},
                 {"role": "assistant", "content": "..."}]
            system: 可选 system prompt，会 prepend 到消息列表最前面。
            **invoke_kwargs: 透传给底层 model.invoke，例如 temperature。

        Returns:
            模型生成的文本字符串。
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        lc_msgs: list[HumanMessage | AIMessage | SystemMessage] = []
        if system:
            lc_msgs.append(SystemMessage(content=system))

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                lc_msgs.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_msgs.append(AIMessage(content=content))
            else:
                lc_msgs.append(HumanMessage(content=content))

        response = self.model.invoke(lc_msgs, **invoke_kwargs)
        return str(response.content)


class BrainRegistry:
    """大脑注册表 —— 按 config 配置实例化所有脑，提供按名取用。"""

    def __init__(
        self,
        config_path: str | Path = "config/config.yaml",
        secrets_path: str | Path = "config/secrets.yaml",
    ) -> None:
        self._config: dict[str, Any] = self._load_yaml(config_path)
        self._secrets: dict[str, Any] = self._load_yaml(secrets_path)
        self._brains: dict[str, Brain] = {}
        self._build_brains()

    # ------------------------------------------------------------------ #
    # 内部 helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_yaml(path: str | Path) -> dict[str, Any]:
        p = Path(path)
        if not p.is_absolute():
            # 相对项目根目录（chuan/ 的上级）
            p = Path(__file__).resolve().parent.parent / p
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _resolve_api_key(self, env_var_name: str | None, fallback: str | None) -> str | None:
        """优先读环境变量，其次 secrets.yaml 里的 fallback 值。"""
        if env_var_name:
            val = os.environ.get(env_var_name)
            if val:
                return val
        if fallback:
            return fallback
        return None

    def _build_brains(self) -> None:
        brains_cfg: dict[str, dict[str, Any]] = self._config.get("brains", {})
        secrets = self._secrets

        for name, cfg in brains_cfg.items():
            provider = cfg.get("provider", "").lower()
            model_id = cfg.get("model", "")
            temperature = cfg.get("temperature", 0.7)

            if provider == "openrouter":
                api_key = self._resolve_api_key(
                    cfg.get("api_key_env"), secrets.get("openrouter_api_key")
                )
                # OpenRouter 兼容 OpenAI API，必须传 model；空则用免费兜底
                chat = ChatOpenAI(
                    model=model_id or "openai/gpt-3.5-turbo",
                    api_key=api_key or "sk-dummy",  # 缺 key 时抛友好错误
                    base_url="https://openrouter.ai/api/v1",
                    temperature=temperature,
                )
                self._brains[name] = Brain(chat, name=name)

            elif provider == "openai":
                # 通用 OpenAI 兼容 API（智谱 BigModel / DeepSeek / 通义千问 / 本地 vLLM 等）
                base_url = cfg.get("base_url", "https://api.openai.com/v1")
                secret_key = cfg.get("api_key_secret", "openai_api_key")
                api_key = self._resolve_api_key(
                    cfg.get("api_key_env"), secrets.get(secret_key)
                )
                chat = ChatOpenAI(
                    model=model_id or "gpt-3.5-turbo",
                    api_key=api_key or "sk-dummy",
                    base_url=base_url,
                    temperature=temperature,
                )
                self._brains[name] = Brain(chat, name=name)

            elif provider == "ollama":
                base_url = cfg.get("base_url", "http://localhost:11434")
                chat = ChatOllama(
                    model=model_id or "qwen2.5:14b",
                    base_url=base_url,
                    temperature=temperature,
                )
                self._brains[name] = Brain(chat, name=name)

            else:
                # 未知 provider，跳过（N1 不报错，留 N9 联调时暴露）
                continue

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #
    def get(self, name: str) -> Brain | None:
        """按名字取脑，不存在返回 None。"""
        return self._brains.get(name)

    def default(self) -> Brain:
        """取默认脑（config.routing.default_brain）。"""
        default_name = self._config.get("routing", {}).get("default_brain", "cloud_general")
        brain = self._brains.get(default_name)
        if brain is None:
            # 兜底：取第一个可用的
            brain = next(iter(self._brains.values()))
        return brain

    def fallback(self) -> Brain | None:
        """取 fallback 脑（config.routing.fallback_brain），无则 None。"""
        fallback_name = self._config.get("routing", {}).get("fallback_brain")
        if fallback_name:
            return self._brains.get(fallback_name)
        return None

    def list(self) -> list[str]:
        """返回已加载的大脑名称列表。"""
        return list(self._brains.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._brains
