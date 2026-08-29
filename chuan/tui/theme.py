"""N17 TUI 主题 —— 角色专属色、品牌渐变、水线动画帧。

配色语义（定稿方案 B）：
- 蓝紫渐变仅用于门面 wordmark 与品牌标识
- 主体颜色按「角色身份」分配：每个班底角色一个专属色，
  对话流与角色条一眼辨认"谁在说话"
"""

from __future__ import annotations

from rich.text import Text

# ---- 基础色板 -------------------------------------------------------- #
BG = "#0b0f1a"        # 终端底色（墨蓝）
SURFACE = "#111830"   # 面板底色
BORDER = "#1e2842"    # 边框
TEXT = "#dce4f2"      # 正文
MUTED = "#7c89a6"     # 工具调用 / 辅助文字
DIM = "#4d5a78"       # 次要信息
BLUE = "#4a9eff"      # 川蓝：管家 / 品牌渐变起点
MID = "#7b8cf9"       # 幕僚长 / 渐变中点
VIOLET = "#a78bfa"    # 岚紫：研究 / 品牌渐变终点
GREEN = "#5fbf8a"     # 成功

DEFAULT_COLOR = "#8b9ecb"  # 用户 / 未知名角色

# ---- 角色专属色（persona 名 + 显示名双索引） -------------------------- #
# 对齐 personas/ 下真实班底角色（A9 版）。
ROLE_COLORS: dict[str, str] = {
    "housekeeper": BLUE,          "管家": BLUE,
    "chief_of_staff": MID,        "幕僚长": MID,
    "researcher": VIOLET,         "研究": VIOLET,
    "programmer": "#34d399",      "IT": "#34d399",
    "lawyer": "#f59e0b",          "律师": "#f59e0b",
    "bodyguard": "#ef4444",       "保镖": "#ef4444",
    "secretary": "#fbbf24",       "秘书": "#fbbf24",
    "investment": "#38bdf8",      "投资": "#38bdf8",
    "finance": "#22d3ee",         "财务": "#22d3ee",
    "tax": "#a3e635",             "税务": "#a3e635",
}

# ---- 角色 ASCII 小像（非 emoji） -------------------------------------- #
ROLE_AVATARS: dict[str, str] = {
    "housekeeper": "⌂",           "管家": "⌂",
    "chief_of_staff": "⚑",        "幕僚长": "⚑",
    "researcher": "◎",            "研究": "◎",
    "programmer": "⌥",            "IT": "⌥",
    "lawyer": "⚖",                "律师": "⚖",
    "bodyguard": "⛨",             "保镖": "⛨",
    "secretary": "✉",             "秘书": "✉",
    "investment": "▲",            "投资": "▲",
    "finance": "₿",               "财务": "₿",
    "tax": "§",                   "税务": "§",
}
_DEFAULT_AVATAR = "○"

# 未知角色的确定性兜底色（按名字哈希取色，同一角色永远同色）
_FALLBACK = ("#6ea8ff", "#9d8bff", "#5fd0c8", "#f0b86e", "#f08fb0", "#8fd3a8")


def role_color(name: str = "", display: str = "") -> str:
    """取角色专属色：persona 名 → 显示名 → 名字哈希兜底。"""
    for key in (name, display):
        if key in ROLE_COLORS:
            return ROLE_COLORS[key]
    seed = display or name
    if seed:
        return _FALLBACK[sum(ord(c) for c in seed) % len(_FALLBACK)]
    return DEFAULT_COLOR


def role_avatar(name: str = "", display: str = "") -> str:
    """取角色 ASCII 小像：persona 名 → 显示名 → 兜底圆点。"""
    for key in (name, display):
        if key in ROLE_AVATARS:
            return ROLE_AVATARS[key]
    return _DEFAULT_AVATAR


# ---- 品牌渐变 -------------------------------------------------------- #
def _rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _lerp(start: str, end: str, t: float) -> str:
    a, b = _rgb(start), _rgb(end)
    mixed = (round(a[0] + (b[0] - a[0]) * t),
             round(a[1] + (b[1] - a[1]) * t),
             round(a[2] + (b[2] - a[2]) * t))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def gradient_text(text: str, start: str = BLUE, end: str = VIOLET) -> Text:
    """逐字符插值的品牌渐变文本（蓝→紫）。"""
    out = Text()
    n = max(len(text) - 1, 1)
    for i, ch in enumerate(text):
        out.append(ch, style=_lerp(start, end, i / n))
    return out


def chuan_mark() -> Text:
    """川字标（品牌 logo）：加粗「川」，用川蓝着色，用于顶栏品牌位。"""
    return Text("川", style=f"bold {BLUE}")


# ---- 水线汇聚动画 ---------------------------------------------------- #
# 三股水流（撇短、中竖最长、右竖收短），启动时逐帧长高后定格
_WATER_FINALS = (3, 7, 5)
_WATER_COLORS = (BLUE, MID, VIOLET)


def waterline_frames(rows: int = 7, steps: int = 8) -> list[Text]:
    """生成水线动画帧：三列水流按进度长高，未长到处显示暗色引导线。

    每列用居中字形的 `█`（已长满）与 `│`（引导线），列间两个空格，
    无尾随空格，确保整块在终端里居中不偏移。
    """
    frames: list[Text] = []
    for step in range(1, steps + 1):
        heights = [max(1, round(f * step / steps)) for f in _WATER_FINALS]
        frame = Text()
        for row in range(rows, 0, -1):
            for i, (h, color) in enumerate(zip(heights, _WATER_COLORS)):
                if i > 0:
                    frame.append("  ")  # 列间空隙
                if row <= h:
                    frame.append("█", style=color)
                else:
                    frame.append("│", style=BORDER)
            if row > 1:
                frame.append("\n")
        frames.append(frame)
    return frames
