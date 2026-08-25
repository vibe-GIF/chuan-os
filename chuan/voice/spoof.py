"""声纹防欺骗（anti-spoof）V1——规则版，不引重型模型。

定位：P4「声纹防欺骗」的第一落地。V1 用 numpy 规则（RMS 能量轮廓 / 时长 /
过零率等统计量）做两级判断：
1. **回放 / 环境噪声特征**（不依赖声纹库）：静音占比过高 / 时长过短 / 能量
   过低 → 直接判 spoof（这是「回放录音 / 单纯环境噪声」的廉价指纹）。
2. **已注册声纹核对**（仅当存在目标声纹时）：对能量轮廓等统计量做相似度打分，
   低于阈值判「声纹不匹配（疑似伪造）」；无注册声纹 → 旁路 ok=True。

约定（对齐 wake_word.py 的 int16 尺度教训）：
- 麦克风流是 float32(-1..1)；openwakeword 需 int16 尺度（×32768）。
- 本模块相反：特征全在 float32(-1..1) 域计算，若收到 int16 输入（峰值 >1）
  自动按 ×1/32768 归一到 float32 域再算，保证阈值语义一致、不崩。

全部失败静默降级返回可读 dict，绝不抛错；后续可换模型后端（pyannote /
ecapa 等），本模块接口不变。
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 16000
# 能量轮廓采样点数（特征向量长度），与查询侧一致便于比对
PROFILE_POINTS = 32
_FRAME_MS = 20  # 帧长 20ms

# 回放 / 环境噪声规则阈值（float32 域）
SILENCE_RMS = 0.004  # 低于此 RMS 视为静音帧（对齐 UtteranceListener 底噪量级）
SILENCE_RATIO_MAX = 0.85  # 静音帧占比高于此 → 静音段回放/空环境
MIN_DURATION = 0.5  # 短于此时长 → 判 spoof（回放碎片 / 误触）
MIN_ENERGY = 0.003  # 平均能量低于此 → 判 spoof（过弱环境噪声）

# 声纹比对权重（能量轮廓相关 + 能量尺度 + 时长 + 过零率）
_W_CORR = 0.50
_W_ENERGY = 0.25
_W_DUR = 0.15
_W_ZCR = 0.10

_DEFAULT_THRESHOLD = 0.7

# 项目根（chuan/voice/spoof.py → chuan/voice → chuan → 根）
_ROOT = Path(__file__).resolve().parent.parent.parent
# 声纹目录（data/speakers /，磁盘真相）
_SPEAKERS_DIR = _ROOT / "data" / "speakers"

_NOTE = (
    "features computed in float32 (-1..1); int16 input is auto-normalized by "
    "*1/32768 (see wake_word.py convention)"
)


def _to_float32(audio: np.ndarray) -> np.ndarray:
    """把音频归一化到 float32(-1..1) 域（int16 尺度输入自动缩放）。

    与 wake_word.py 相反——那边需要 int16（×32768），这边统一 float32 域
    以保证阈值语义可预期。
    """
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return x
    peak = float(np.abs(x).max())
    if peak > 1.0:  # int16 尺度
        x = x / 32768.0
    return np.clip(x, -1.0, 1.0)


def _safe_name(name: str) -> str | None:
    """校验声纹名：空名 / 含路径分隔符 / 含「..」→ 拒（防路径穿越）。"""
    if not isinstance(name, str):
        return None
    s = name.strip()
    if not s or len(s) > 64:
        return None
    if any(ch in s for ch in ("/", "\\")) or ".." in s:
        return None
    return s


def extract_features(
    audio: np.ndarray, sample_rate: int = SAMPLE_RATE
) -> dict[str, Any]:
    """提特征（V1 numpy 规则版）。

    返回含固定长度能量轮廓向量（PROFILE_POINTS）+ 标量统计量的 dict：
    - vector：归一化 RMS 能量轮廓，长度 PROFILE_POINTS
    - rms_mean / rms_std / peak：能量统计（float32 域）
    - zcr：过零率
    - silence_ratio：静音帧占比
    - duration_s：时长秒数
    """
    empty: dict[str, Any] = {
        "vector": np.zeros(PROFILE_POINTS, dtype=np.float32).tolist(),
        "rms_mean": 0.0,
        "rms_std": 0.0,
        "peak": 0.0,
        "zcr": 0.0,
        "silence_ratio": 1.0,
        "duration_s": 0.0,
    }
    try:
        x = _to_float32(audio)
        if x.size == 0:
            return empty

        frame_len = max(1, int(sample_rate * _FRAME_MS / 1000))
        n_frames = max(1, x.size // frame_len)
        frames = x[: n_frames * frame_len].reshape(n_frames, frame_len)
        frame_rms = np.sqrt(np.mean(frames**2, axis=1))

        peak = float(np.abs(x).max())
        rms_mean = float(frame_rms.mean())
        rms_std = float(frame_rms.std())
        zcr = float(np.mean(np.abs(np.diff(np.sign(x)) > 0.0)) if x.size > 1 else 0.0)
        silence_ratio = float(np.mean(frame_rms < SILENCE_RMS) if frame_rms.size else 1.0)

        # 能量轮廓：重采样到固定长度（分段平均），除以峰值归一化（尺度无关）
        if frame_rms.size == 0 or frame_rms.max() <= 1e-9:
            profile = np.zeros(PROFILE_POINTS, dtype=np.float32)
        else:
            if frame_rms.size >= PROFILE_POINTS:
                groups = np.array_split(frame_rms, PROFILE_POINTS)
                profile = np.array(
                    [float(g.mean()) if g.size else 0.0 for g in groups],
                    dtype=np.float32,
                )
            else:
                profile = frame_rms[:PROFILE_POINTS].astype(np.float32)
            profile = profile / float(frame_rms.max())

        return {
            "vector": profile.tolist(),
            "rms_mean": rms_mean,
            "rms_std": rms_std,
            "peak": peak,
            "zcr": zcr,
            "silence_ratio": silence_ratio,
            "duration_s": float(x.size / max(1, sample_rate)),
        }
    except Exception:  # noqa: BLE001 - 特征提取失败静默降级
        return empty


def _closeness(a: float, b: float, eps: float = 1e-6) -> float:
    """两个正标量的贴近度 [0,1]：min(a,b)/max(a,b)。"""
    a, b = max(float(a), eps), max(float(b), eps)
    return float(min(a, b) / max(a, b))


def _compare_voiceprint(enrolled: dict[str, Any], fx: dict[str, Any]) -> float:
    """已注册声纹 ↔ 查询特征的相似度 [0,1]（V1 规则，numpy 相关 + 标量贴近）。"""
    try:
        ref = np.asarray(enrolled.get("vector") or [], dtype=np.float32)
        q = np.asarray(fx.get("vector") or [], dtype=np.float32)
        if ref.size == 0 or q.size == 0:
            corr = 0.5
        else:
            n = min(ref.size, q.size)
            r1 = ref[:n].astype(np.float32)
            r2 = q[:n].astype(np.float32)
            if float(np.std(r1)) <= 1e-9 or float(np.std(r2)) <= 1e-9:
                corr = 0.5
            else:
                corr = float(np.corrcoef(r1, r2)[0, 1])
                corr = (corr + 1.0) / 2.0  # [-1,1] → [0,1]

        energy_sim = _closeness(
            float(enrolled.get("rms_mean", 0.0)), float(fx.get("rms_mean", 0.0))
        )
        dur_sim = _closeness(
            float(enrolled.get("duration_s", 0.0)), float(fx.get("duration_s", 0.0))
        )
        zcr_sim = _closeness(float(enrolled.get("zcr", 0.0)), float(fx.get("zcr", 0.0)))
        return float(
            _W_CORR * corr
            + _W_ENERGY * energy_sim
            + _W_DUR * dur_sim
            + _W_ZCR * zcr_sim
        )
    except Exception:  # noqa: BLE001 - 比对失败给中性分，不抛错
        return 0.5


def _file_path(name: str) -> Path | None:
    safe = _safe_name(name)
    if safe is None:
        return None
    return _SPEAKERS_DIR / f"{safe}.json"


def enroll_speaker(
    name: str, audio: np.ndarray, sample_rate: int = SAMPLE_RATE
) -> bool:
    """注册声纹：提特征 → 写 data/speakers/<name>.json。成功返回 True。"""
    try:
        safe = _safe_name(name)
        if safe is None:
            return False
        fx = extract_features(audio, sample_rate)
        if fx["duration_s"] < MIN_DURATION or fx["peak"] <= 0.0:
            return False  # 过短/全静音不作为有效声纹入库
        _SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)
        doc = {
            "name": safe,
            "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "feature": fx,
            "_note": _NOTE,
        }
        path = _file_path(safe)
        if path is None:
            return False
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # 原子落盘，防半截 JSON
        return True
    except Exception:  # noqa: BLE001
        return False


def load_speaker(name: str) -> dict[str, Any] | None:
    """读取注册声纹。不存在 / 损坏 / 非法名 → None。"""
    try:
        path = _file_path(name)
        if path is None or not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("feature") or None
    except Exception:  # noqa: BLE001
        return None


def list_speakers() -> list[str]:
    """列出已注册声纹名（data/speakers/ 磁盘真相）。"""
    try:
        if not _SPEAKERS_DIR.exists():
            return []
        return sorted(
            p.name[: -len(".json")]
            for p in _SPEAKERS_DIR.glob("*.json")
            if p.name != ".gitkeep"
        )
    except Exception:  # noqa: BLE001
        return []


def remove_speaker(name: str) -> bool:
    """删除注册声纹。成功返回 True。"""
    try:
        path = _file_path(name)
        if path is None:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


def anti_spoof(
    audio: np.ndarray,
    name: str | None = None,
    threshold: float = _DEFAULT_THRESHOLD,
    sample_rate: int = SAMPLE_RATE,
    *,
    silence_ratio_max: float = SILENCE_RATIO_MAX,
    min_duration: float = MIN_DURATION,
    min_energy: float = MIN_ENERGY,
) -> dict[str, Any]:
    """反欺骗检测。

    返回 `{ok, score, reason}`：
    - 回放 / 环境噪声特征命中（静音过多 / 过短 / 能量过低）→ ok=False、spoof；
    - 有已注册声纹 → 相似度打分，低于 threshold → ok=False（疑似伪造）；
    - 无注册声纹（name=None 或目标不存在）→ ok=True 但 reason="未注册，跳过"（旁路）。

    全程静默降级，绝不抛错。
    """
    base: dict[str, Any] = {"ok": True, "score": 1.0, "reason": "通过"}
    try:
        fx = extract_features(audio, sample_rate)

        # —— 第一级：回放 / 环境噪声（不依赖声纹库）——
        if fx["duration_s"] < min_duration:
            return {
                "ok": False,
                "score": 0.0,
                "reason": f"音频过短（{fx['duration_s']:.2f}s），疑似回放碎片或误触",
            }
        if fx["silence_ratio"] >= silence_ratio_max:
            return {
                "ok": False,
                "score": 0.0,
                "reason": f"静音占比过高（{fx['silence_ratio']:.0%}），疑似回放/空环境",
            }
        if fx["rms_mean"] < min_energy:
            return {
                "ok": False,
                "score": 0.0,
                "reason": f"能量过低（rms={fx['rms_mean']:.5f}），疑似环境噪声",
            }

        # —— 第二级：已注册声纹核对 ——
        if name is None:
            return {"ok": True, "score": 1.0, "reason": "未注册，跳过"}
        enrolled = load_speaker(name)
        if enrolled is None:
            return {"ok": True, "score": 1.0, "reason": f"未注册声纹「{name}」，跳过"}
        score = _compare_voiceprint(enrolled, fx)
        if math.isnan(score):
            score = 0.0
        if score >= threshold:
            return {"ok": True, "score": score, "reason": "声纹验证通过"}
        return {
            "ok": False,
            "score": score,
            "reason": f"声纹不匹配（{score:.2f} < {threshold}），疑似伪造",
        }
    except Exception:  # noqa: BLE001 - 任何失败静默旁路通过
        return base