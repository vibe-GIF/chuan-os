"""联网搜索 handler —— 百炼 qwen 联网搜索为主，DuckDuckGo 免费兜底。

被 skills/web_search.yaml 和 skills/read_webpage.yaml 引用，
通过 SkillRegistry 包装为 LangChain Tool。

两级搜索策略:
1. 阿里云百炼 qwen-flash + enable_search（联网搜索增强，
   返回基于实时搜索结果的综合答复，中文质量好）
2. ddgs（DuckDuckGo 免费搜索，无需 API key，返回结构化
   标题/链接/摘要列表）——百炼不可用/失败时兜底

read_webpage: 抓取指定 URL 并提取正文文本（lxml），
配合 ddgs 返回的链接深读网页内容。
"""

from __future__ import annotations

import re
from pathlib import Path

import requests
import yaml

# 项目根目录（secrets.yaml 相对于 skills/handlers 上两级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SECRETS_PATH = _PROJECT_ROOT / "config" / "secrets.yaml"

_BAILIAN_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
)
_BAILIAN_TIMEOUT = 60  # 联网搜索要真实抓网页，耗时比纯 LLM 长

# N44：进程级默认缓存（Redis 后端 + 内存兜底；未启用/不可达时 no-op/兜底）
_SEARCH_TTL = 3600  # 搜索结果缓存 1 小时
try:
    from chuan.cache import get_cache

    _cache = get_cache()
except Exception:  # noqa: BLE001 - chuan 不可导入时退化为无缓存
    _cache = None


def _load_bailian_key() -> str:
    """读百炼 API key：环境变量优先，其次 config/secrets.yaml。"""
    import os

    key = os.environ.get("BAILIAN_API_KEY", "")
    if key:
        return key
    try:
        secrets = yaml.safe_load(_SECRETS_PATH.read_text(encoding="utf-8")) or {}
        return str(secrets.get("bailian_api_key", "") or "")
    except Exception:  # noqa: BLE001 - 凭证读取失败走免费兜底
        return ""


def _search_bailian(query: str) -> str:
    """百炼联网搜索：qwen-flash + enable_search，返回综合答复。"""
    key = _load_bailian_key()
    if not key:
        raise RuntimeError("未配置 bailian_api_key")

    resp = requests.post(
        _BAILIAN_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "qwen-flash",
            "input": {"messages": [{"role": "user", "content": query}]},
            "parameters": {"result_format": "message", "enable_search": True},
        },
        timeout=_BAILIAN_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["output"]["choices"][0]["message"]["content"]
    if not str(content).strip():
        raise RuntimeError("百炼搜索返回空内容")
    return str(content)


def _search_ddgs(query: str, max_results: int) -> str:
    """DuckDuckGo 免费搜索：返回结构化标题/链接/摘要列表。"""
    from ddgs import DDGS

    results = DDGS().text(query, max_results=max_results)
    if not results:
        return f"[搜索无结果] {query}"
    lines = []
    for i, item in enumerate(results, 1):
        title = str(item.get("title", "")).strip()
        href = str(item.get("href", "")).strip()
        body = str(item.get("body", "")).strip()
        lines.append(f"{i}. {title}\n   {href}\n   {body}")
    return "\n".join(lines)


def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索并返回结果。

    优先用阿里云百炼联网搜索（返回综合答复），失败时退回
    DuckDuckGo 免费搜索（返回标题/链接/摘要列表）。

    Args:
        query: 搜索关键词或问题（中文最佳）
        max_results: ddgs 兜底模式的结果条数

    Returns:
        搜索结果文本；两条路都失败时返回错误说明。
    """
    if not query or not query.strip():
        return "[ERROR] 搜索词不能为空"

    query = query.strip()
    cache_key = f"search:{query}"
    if _cache is not None:
        cached = _cache.get(cache_key)
        if cached:
            return cached

    # 1) 百炼联网搜索
    try:
        result = _search_bailian(query)
        if _cache is not None:
            _cache.set(cache_key, result, ttl=_SEARCH_TTL)
        return result
    except Exception:  # noqa: BLE001 - 主路失败，静默降级
        pass

    # 2) ddgs 免费兜底
    try:
        result = _search_ddgs(query, max_results)
        if _cache is not None and not result.startswith("[搜索无结果]"):
            _cache.set(cache_key, result, ttl=_SEARCH_TTL)
        return result
    except Exception as exc:  # noqa: BLE001 - 兜底也失败才报错
        return f"[ERROR] 搜索失败：{exc}。可稍后重试或换关键词。"


_NOISE_BLOCKS = (
    "script",
    "style",
    "head",
    "nav",
    "header",
    "footer",
    "aside",
    "menu",
    "noscript",
    "iframe",
    "form",
    "template",
)


def _extract_text(html: str) -> str:
    """通用 HTML 正文提取：剥掉脚本/样式/导航/页脚等噪声块，只留正文。"""
    text = html
    for tag in _NOISE_BLOCKS:
        text = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>", " ", text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    text = re.sub(r"<[^>]+>", " ", text)  # 残留标签 → 空格
    return re.sub(r"\s+", " ", text).strip()


def read_webpage(url: str, max_chars: int = 4000) -> str:
    """抓取网页并提取正文文本（去掉导航/脚本/样式）。

    配合 web_search 使用：ddgs 兜底模式返回链接后，可用本工具
    深读指定网页的完整内容。

    Args:
        url: 网页地址（http/https）
        max_chars: 返回正文的最大字符数（默认 4000）

    Returns:
        网页正文文本；失败时返回错误说明。
    """
    if not url or not re.match(r"^https?://", url.strip()):
        return "[ERROR] 请提供 http/https 开头的网址"

    try:
        resp = requests.get(
            url.strip(),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 网络失败给模型明确信号
        return f"[ERROR] 网页抓取失败：{exc}"

    try:
        # 编码修正：requests 对无 charset 头的响应默认 latin-1（中文必乱码），
        # 用 apparent_encoding（chardet 猜测）覆盖后再取 text
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        text = _extract_text(resp.text)
    except Exception:  # noqa: BLE001 - 编码/解析异常退回原文截断
        text = resp.text[:max_chars]

    if not text.strip():
        return "[ERROR] 网页没有可提取的正文"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n…（截断，原文共 {len(text)} 字符）"
    return text
