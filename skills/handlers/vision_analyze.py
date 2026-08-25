"""视觉理解 handler —— 把本地图片 / 图片 URL 发给视觉模型（qwen-vl / glm-4v），返回文字描述。

被 skills/vision_analyze.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。

设计（N50 视觉理解）:
- 无新增依赖（用既有 openai 客户端，OpenAI 兼容视觉消息：text + image_url data URI）；
- 图片不存在 / 读不到 / 模型不可用 / key 缺失 → 返回可读错误文本，**绝不抛错**（静默降级）；
- 视觉模型配置读 config.yaml 的 `brains.vision`（provider openai，qwen-vl-plus），
  key 解析对齐 brains（先 env api_key_env，再 secrets.yaml 的 api_key_secret）。
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

# 项目根目录（解析相对路径与 config）
_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PROMPT = "请详细描述这张图片的内容：主体、物体、文字（OCR）、场景与布局。"


def _load_vision_cfg() -> dict:
    """读取 config.yaml 的 brains.vision 段；读不到返回空 dict。"""
    p = _ROOT / "config" / "config.yaml"
    if not p.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(p.open("r", encoding="utf-8")) or {}
        return (data.get("brains") or {}).get("vision") or {}
    except Exception:  # noqa: BLE001 - 配置读不到按未配置处理
        return {}


def _resolve_api_key(cfg: dict) -> str:
    """key 解析：优先环境变量，其次 secrets.yaml 的 api_key_secret 字段。"""
    env_var = cfg.get("api_key_env")
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    secret_field = cfg.get("api_key_secret")
    if secret_field:
        try:
            import yaml

            secrets = yaml.safe_load(
                (_ROOT / "config" / "secrets.yaml").open("r", encoding="utf-8")
            ) or {}
            val = secrets.get(secret_field)
            if val:
                return str(val)
        except Exception:  # noqa: BLE001
            pass
    return ""


def _image_data_uri(path: Path) -> str:
    """本地图片 → data URI（mime 由扩展名猜，默认 image/png）。"""
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _call_vision(model: str, base_url: str, api_key: str, image_url: str, prompt: str) -> str:
    """调 OpenAI 兼容视觉模型，返回回复文本。"""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        max_tokens=800,
    )
    return (resp.choices[0].message.content or "").strip()


def vision_analyze(image_ref: str = "") -> str:
    """视觉理解：分析本地图片文件或图片 URL，返回文字描述。

    Args:
        image_ref: 本地图片路径（相对项目根或绝对路径），或 http(s) 图片 URL。

    Returns:
        视觉模型返回的描述文本；任何失败返回可读错误提示（静默降级，不抛错）。
    """
    image_ref = (image_ref or "").strip()
    if not image_ref:
        return "（视觉理解：未提供图片路径或 URL）"

    cfg = _load_vision_cfg()
    model = str(cfg.get("model") or "qwen-vl-plus")
    base_url = str(
        cfg.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    api_key = _resolve_api_key(cfg)
    if not api_key:
        return (
            "（视觉理解：未配置视觉大脑 api_key——config brains.vision 或 "
            "环境变量 BAILIAN_API_KEY，无法调用视觉模型）"
        )

    # 解析图片引用 → data URI（本地文件）或直用 URL
    image_url = image_ref
    if not image_ref.lower().startswith(("http://", "https://")):
        p = Path(image_ref)
        if not p.is_absolute():
            p = _ROOT / p
        if not p.is_file():
            return f"（视觉理解：图片不存在 - {image_ref}）"
        try:
            image_url = _image_data_uri(p)
        except Exception as exc:  # noqa: BLE001 - 读取失败转可读错误
            return f"（视觉理解：读取图片失败 - {exc}）"

    try:
        text = _call_vision(model, base_url, api_key, image_url, DEFAULT_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 模型调用失败转可读错误
        return f"（视觉理解失败：{exc}）"
    return text or "（视觉理解：模型返回为空）"

