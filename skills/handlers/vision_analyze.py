"""视觉理解 handler —— 把本地图片 / 图片 URL / PDF / 表格(csv) / 录屏视频(mp4/mov/mkv)
发给视觉模型（qwen-vl / glm-4v），返回文字描述。

被 skills/vision_analyze.yaml 引用，通过 SkillRegistry 包装为 LangChain Tool。

设计（N50 视觉理解，N52 扩展 PDF/表格/视频转图）:
- 用既有 openai 客户端，OpenAI 兼容视觉消息（text + image_url data URI）；
- V2 扩展（N52）：PDF / 表格(csv) / 录屏视频 先转成图再走同一条视觉管线；
- 图片不存在 / 读不到 / 转换失败 / 缺依赖 / 模型不可用 / key 缺失 → 返回可读错误文本，**绝不抛错**（静默降级）；
- 视觉模型配置读 config.yaml 的 `brains.vision`（provider openai，qwen-vl-plus），
  key 解析对齐 brains（先 env api_key_env，再 secrets.yaml 的 api_key_secret）。
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# 项目根目录（解析相对路径与 config）
_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PROMPT = "请详细描述这张图片的内容：主体、物体、文字（OCR）、场景与布局。"

# V2（N52）扩展的文件类型分类
_PDF_SUFFIXES = (".pdf",)
_TABLE_SUFFIXES = (".xlsx", ".xls", ".csv")
_VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".avi", ".webm")

# ffmpeg 路径缓存（False=未探测；None=无）——对齐 chuan/voice/tts.py 的 _ffmpeg_bin 惯例
_FFMPEG_BIN: str | None | bool = False


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


# --------------------------------------------------------------------- #
# N52 扩展：PDF / 表格(csv) / 视频 → 图片 data URI（缺依赖静默降级）
# --------------------------------------------------------------------- #
def _ffmpeg_bin() -> str | None:
    """解析 ffmpeg 路径：FFMPEG_BIN 环境变量 → imageio-ffmpeg 自带 → 系统 PATH。"""
    global _FFMPEG_BIN
    if _FFMPEG_BIN is not False:
        return _FFMPEG_BIN or None
    cand = os.environ.get("FFMPEG_BIN")
    if not cand:
        try:
            import imageio_ffmpeg  # type: ignore[import-not-found]

            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.isfile(exe):
                cand = exe
        except Exception:  # noqa: BLE001 - 未安装走系统兜底
            pass
    if not cand:
        cand = shutil.which("ffmpeg")
    _FFMPEG_BIN = cand or None
    return cand


def _missing_dep(dep: str, action: str) -> str:
    """缺依赖的可读降级提示（静默降级，不抛错）。"""
    return f"（视觉理解：需安装 {dep} 才能{action}，已跳过当前文件）"


def _bytes_to_jpeg_data_uri(raw: bytes) -> str:
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _pdf_to_img(p: Path) -> tuple[str | None, str | None]:
    """PDF → 首页图（jpg）；缺 pdf2image/ghostscript 或转换失败返回 (None, 可读提示)。"""
    try:
        import pdf2image
    except Exception:  # noqa: BLE001 - 缺依赖静默降级
        return None, _missing_dep("pdf2image（及 poppler/ghostscript）", "转 PDF 首页为图片")
    try:
        images = pdf2image.convert_from_path(str(p), first_page=1, last_page=1)
        if not images:
            return None, f"（视觉理解：PDF 无可渲染页面 - {p.name}）"
        buf = io.BytesIO()
        images[0].save(buf, format="JPEG")
        return _bytes_to_jpeg_data_uri(buf.getvalue()), None
    except Exception as exc:  # noqa: BLE001 - 转换失败转可读提示
        return None, f"（视觉理解：PDF 转图失败 - {exc}）"


def _csv_rows(p: Path) -> list[list[str]]:
    """读 csv → 单元格字符串二维表（优先 pandas，缺则 stdlib csv）。"""
    try:
        import pandas as pd  # type: ignore[import-not-found]

        df = pd.read_csv(p)
        header = [str(c) for c in df.columns]
        rows = [
            ["" if pd.isna(v) else str(v) for v in row]
            for row in df.itertuples(index=False)
        ]
        return [header] + rows
    except Exception:  # noqa: BLE001 - pandas 缺或读失败，退回 stdlib
        import csv

        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return [[c or "" for c in list(row)] for row in csv.reader(fh)]


def _table_to_img(p: Path) -> tuple[str | None, str | None]:
    """表格 → 表格图（jpg）。V1 只渲染 csv；xlsx 留提示；缺 pillow 降级。"""
    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return None, "（视觉理解：xlsx 暂未支持转图，请先另存为 csv 再分析；V1 仅支持 csv 渲染）"
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - 缺依赖静默降级
        return None, _missing_dep("pillow/PIL", "把 csv 渲染成表格图")
    try:
        rows = _csv_rows(p)
    except Exception as exc:  # noqa: BLE001
        return None, f"（视觉理解：读取表格失败 - {exc}）"
    if not rows:
        return None, "（视觉理解：表格为空，无内容可渲染）"

    font = ImageFont.load_default()
    cell_w, cell_h, pad = 160, 26, 6
    max_cols = max(len(r) for r in rows)
    img_w = max(320, cell_w * max_cols + 4)
    img_h = cell_h * len(rows) + 6
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)
    for ri, row in enumerate(rows):
        for ci in range(max_cols):
            x0, y0 = ci * cell_w + 2, ri * cell_h + 2
            x1, y1 = x0 + cell_w - 2, y0 + cell_h - 2
            draw.rectangle([x0, y0, x1, y1], outline="black", width=1)
            if ri == 0:
                draw.rectangle([x0, y0, x1, y1], fill="lightgray")
            text = row[ci] if ci < len(row) else ""
            draw.text((x0 + pad, y0 + 3), text[:18], fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return _bytes_to_jpeg_data_uri(buf.getvalue()), None


def _ffmpeg_extract_first_frame(p: Path) -> str | None:
    """用 ffmpeg 抽视频首帧 → jpg data URI；失败返回 None（静默降级）。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="chuan_vframe_"))
    out = tmpdir / "frame.jpg"
    try:
        proc = subprocess.run(
            [
                _ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(p), "-frames:v", "1", str(out),
            ],
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0 or not out.is_file():
            return None
        return _bytes_to_jpeg_data_uri(out.read_bytes())
    except Exception:  # noqa: BLE001 - 抽帧失败静默降级
        return None
    finally:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass


def _video_first_frame(p: Path) -> tuple[str | None, str | None]:
    """视频 → 首帧图；缺 ffmpeg 或抽帧失败返回 (None, 可读提示)。"""
    if not _ffmpeg_bin():
        return None, _missing_dep("ffmpeg", "抽取视频首帧")
    uri = _ffmpeg_extract_first_frame(p)
    if uri is None:
        return None, f"（视觉理解：视频抽帧失败（ffmpeg 不可用或文件损坏）- {p.name}）"
    return uri, None


def _resource_to_data_uri(p: Path) -> tuple[str | None, str | None]:
    """按扩展名把本地资源转成图片 data URI；返回 (uri, 失败提示)，二者恰一为 None。"""
    suffix = p.suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        return _video_first_frame(p)
    if suffix in _PDF_SUFFIXES:
        return _pdf_to_img(p)
    if suffix in _TABLE_SUFFIXES:
        return _table_to_img(p)
    # 默认按图片处理
    try:
        return _image_data_uri(p), None
    except Exception as exc:  # noqa: BLE001 - 读取失败转可读错误
        return None, f"（视觉理解：读取图片失败 - {exc}）"


def vision_analyze(image_ref: str = "") -> str:
    """视觉理解：分析本地图片 / 图片 URL / PDF / 表格(csv) / 录屏视频首帧，返回文字描述。

    Args:
        image_ref: 本地路径（相对项目根或绝对）：支持 .png/.jpg 等图片、
            .pdf（转首页图）、.csv（渲染成表格图）、.mp4/.mov/.mkv（抽首帧）；
            或 http(s) 图片 URL。

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

    # 解析引用 → data URI（本地文件转图）或直用 http(s) URL
    if image_ref.lower().startswith(("http://", "https://")):
        image_url = image_ref
    else:
        p = Path(image_ref)
        if not p.is_absolute():
            p = _ROOT / p
        if not p.is_file():
            return f"（视觉理解：图片不存在 - {image_ref}）"
        image_url, conv_msg = _resource_to_data_uri(p)
        if image_url is None:
            return conv_msg or "（视觉理解：无法把该文件转换为图片）"

    try:
        text = _call_vision(model, base_url, api_key, image_url, DEFAULT_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 模型调用失败转可读错误
        return f"（视觉理解失败：{exc}）"
    return text or "（视觉理解：模型返回为空）"