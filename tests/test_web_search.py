"""联网搜索 handler 测试（百炼主路 + ddgs 兜底 + 网页阅读，全 mock 不碰网络）。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# handlers 包在 skills/ 目录下（SkillRegistry 运行时也会把它加进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))

from handlers.web_search import read_webpage, web_search  # noqa: E402

# 测试封闭：禁用模块级缓存（config cache.enabled 打开 + 真实 Redis 在线时，
# 相同 query 的搜索结果会落到共享缓存，跨测试命中造成串扰 → 一律置 None 走纯函数）
import handlers.web_search as _ws_module

_ws_module._cache = None

# ddgs 是可选依赖；未安装时注入占位模块，使 patch("ddgs.DDGS") 能解析目标，
# 各测试再用 patch 替换 DDGS 的具体行为。
if "ddgs" not in sys.modules:
    _ddgs = types.ModuleType("ddgs")
    _ddgs.DDGS = type("DDGS", (), {})
    sys.modules["ddgs"] = _ddgs


def _fake_bailian_resp(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "output": {"choices": [{"message": {"content": content}}]}
    }
    return resp


# ── web_search：百炼主路 ─────────────────────────────


def test_web_search_bailian_primary() -> None:
    with patch("handlers.web_search.requests.post") as mock_post:
        mock_post.return_value = _fake_bailian_resp("搜索结果：答案A")
        result = web_search("中国新能源汽车竞品")
    assert result == "搜索结果：答案A"
    # 请求体里开了联网搜索
    body = mock_post.call_args[1]["json"]
    assert body["parameters"]["enable_search"] is True


def test_web_search_bailian_empty_key_falls_back_to_ddgs() -> None:
    """无 key：不走百炼，直接 ddgs 兜底。"""
    ddgs = SimpleNamespace(
        text=lambda q, max_results=5: [
            {"title": "结果1", "href": "https://a.com", "body": "摘要1"}
        ]
    )
    with (
        patch("handlers.web_search._load_bailian_key", return_value=""),
        patch("handlers.web_search.requests.post") as mock_post,
        patch("ddgs.DDGS", return_value=ddgs),
    ):
        result = web_search("随便查点什么")
    assert mock_post.call_count == 0  # 没 key 不发请求
    assert "结果1" in result and "https://a.com" in result


def test_web_search_bailian_failure_falls_back_to_ddgs() -> None:
    """百炼挂了：静默降级 ddgs。"""
    ddgs = SimpleNamespace(
        text=lambda q, max_results=5: [
            {"title": "兜底结果", "href": "https://b.com", "body": "摘要"}
        ]
    )
    with (
        patch("handlers.web_search._load_bailian_key", return_value="sk-xxx"),
        patch("handlers.web_search.requests.post", side_effect=RuntimeError("网络断开")),
        patch("ddgs.DDGS", return_value=ddgs),
    ):
        result = web_search("随便查点什么")
    assert "兜底结果" in result


def test_web_search_both_fail_returns_error() -> None:
    with (
        patch("handlers.web_search._load_bailian_key", return_value=""),
        patch("ddgs.DDGS", side_effect=RuntimeError("被墙了")),
    ):
        result = web_search("随便查点什么")
    assert result.startswith("[ERROR] 搜索失败")


def test_web_search_empty_query() -> None:
    assert web_search("  ") == "[ERROR] 搜索词不能为空"


# ── read_webpage ────────────────────────────────────


def test_read_webpage_extracts_text_and_strips_noise() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.encoding = None
    resp.apparent_encoding = "utf-8"
    html = (
        "<html><head><style>body{}</style><script>var x=1;</script></head>"
        "<body><nav>menu</nav><h1>标题</h1><p>正文内容第一段。</p>"
        "<footer>footer</footer></body></html>"
    )
    resp.text = html
    with patch("handlers.web_search.requests.get", return_value=resp):
        text = read_webpage("https://example.com/a")
    assert "正文内容第一段" in text
    assert "var x=1" not in text  # script 被剥掉
    assert "menu" not in text  # nav 被剥掉
    assert "footer" not in text  # footer 被剥掉


def test_read_webpage_truncates_long_text() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.encoding = "utf-8"
    resp.text = "<html><body><p>" + "x" * 10000 + "</p></body></html>"
    with patch("handlers.web_search.requests.get", return_value=resp):
        text = read_webpage("https://example.com/long", max_chars=100)
    assert len(text) < 200
    assert "截断" in text


def test_read_webpage_rejects_bad_url() -> None:
    assert read_webpage("not-a-url").startswith("[ERROR]")


def test_read_webpage_network_error() -> None:
    with patch("handlers.web_search.requests.get", side_effect=RuntimeError("超时")):
        assert read_webpage("https://example.com").startswith("[ERROR]")


# ── 技能注册（yaml → LangChain Tool） ───────────────


def test_web_search_skills_registered_as_tools() -> None:
    from chuan.adapters.skill_loader import SkillRegistry

    reg = SkillRegistry("skills")
    names = [t.name for t in reg.get_tools()]
    assert "web_search" in names
    assert "read_webpage" in names


def test_researcher_does_not_deny_search_tools() -> None:
    """研究岗的 deny 列表不能屏蔽搜索工具（调研刚需）。"""
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(
        (Path("personas/researcher/config.yaml")).read_text(encoding="utf-8")
    )
    deny = set(cfg.get("deny", []))
    assert "web_search" not in deny
    assert "read_webpage" not in deny
