"""合同审查本地 handler —— 被 contract_review.yaml 调用。

绕过 MCP，直接跑 Python 实现复杂逻辑。
基于关键词正则匹配的基础合同风险扫描，不依赖 LLM。

可独立运行：
    python skills/handlers/legal_scan.py <合同文件路径>
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# 单项风险检测规则
# ---------------------------------------------------------------------------

def _check_penalty(text: str) -> list[dict]:
    """违约金比例过高（>30%）。"""
    risks: list[dict] = []

    # 形式一：违约金在前，百分比在后 —— "违约金为合同金额 30%"
    pattern_pct_after = re.compile(
        r"违约金[^。\n；;]{0,60}?(\d+(?:\.\d+)?)\s*%",
    )
    # 形式二：百分比在前，违约金在后 —— "30% 的违约金"
    pattern_pct_before = re.compile(
        r"(\d+(?:\.\d+)?)\s*%[^。\n；;]{0,60}?违约金",
    )
    # 形式三：违约金在前，中文百分比在后 —— "违约金为百分之三十"
    pattern_cn_after = re.compile(
        r"违约金[^。\n；;]{0,60}?百分之(\d+(?:\.\d+)?)",
    )
    # 形式四：中文百分比在前，违约金在后 —— "百分之三十的违约金"
    pattern_cn_before = re.compile(
        r"百分之(\d+(?:\.\d+)?)[^。\n；;]{0,60}?违约金",
    )

    seen_spans: set[tuple[int, int]] = set()
    for pat in (pattern_pct_after, pattern_pct_before, pattern_cn_after, pattern_cn_before):
        for m in pat.finditer(text):
            pct = float(m.group(1))
            if pct > 30:
                span = (m.start(), m.end())
                # 同一文本片段被多个模式命中时去重
                if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
                    continue
                seen_spans.add(span)
                risks.append({
                    "type": "违约金比例过高",
                    "severity": "high",
                    "description": (
                        f"检测到违约金比例 {pct}%，超过 30% 的常见合理上限，"
                        "可能被法院认定过高而调减，或构成显失公平。"
                    ),
                })

    return risks


def _check_unilateral_termination(text: str) -> list[dict]:
    """无条件解约权 / 单方任意解除权。"""
    risks: list[dict] = []
    patterns = [
        r"无条件\s*(?:解除|解约|终止)",
        r"随时\s*(?:解除|解约|终止)(?:\s*合同|\s*协议)?",
        r"单方\s*(?:有权|可以)\s*(?:解除|解约|终止)",
        r"任意\s*(?:解除|解约|终止)\s*权",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            risks.append({
                "type": "无条件解约权",
                "severity": "high",
                "description": (
                    f"检测到「{m.group()}」条款，可能赋予对方无条件或单方任意解约权，"
                    "导致合同稳定性丧失，需确认是否附加提前通知期或补偿条件。"
                ),
            })
    return risks


def _check_ip_transfer(text: str) -> list[dict]:
    """知识产权全部转让。"""
    risks: list[dict] = []
    patterns = [
        r"知识产权\s*(?:全部|所有)\s*(?:转让|归|归属)",
        r"(?:全部|所有)\s*知识产权\s*(?:转让|归|归属)",
        r"著作权[^。\n；;]{0,20}?(?:全部|所有)[^。\n；;]{0,20}?(?:转让|归)",
        r"专利[^。\n；;]{0,20}?(?:全部|所有)[^。\n；;]{0,20}?(?:转让|归)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            risks.append({
                "type": "知识产权全部转让",
                "severity": "high",
                "description": (
                    f"检测到「{m.group()}」条款，可能涉及知识产权全部转让，"
                    "需确认是否保留己方背景知识产权及后续改进成果的权属。"
                ),
            })
    return risks


def _check_jurisdiction(text: str) -> list[dict]:
    """争议管辖地在外地（约定特定管辖法院/仲裁机构时提示核实）。"""
    risks: list[dict] = []
    patterns = [
        (r"(?:由|向)\s*([^，。\n；;]{2,30}?)\s*(?:人民)?法院\s*(?:管辖|起诉|诉讼)", 1),
        (r"管辖\s*法院\s*(?:为|是)\s*([^，。\n；;]{2,30})", 1),
        (r"提交\s*([^，。\n；;]{2,30}?)\s*仲裁(?:委员会)?", 1),
        (r"仲裁\s*(?:机构|委员会)\s*(?:为|是)\s*([^，。\n；;]{2,30})", 1),
    ]
    for pat, group_idx in patterns:
        for m in re.finditer(pat, text):
            location = m.group(group_idx).strip()
            risks.append({
                "type": "争议管辖地在外地",
                "severity": "medium",
                "description": (
                    f"检测到争议解决条款约定管辖地为「{location}」，"
                    "需确认该地点是否为己方所在地；若为外地将增加差旅及维权成本。"
                ),
            })
    return risks


def _check_confidentiality_duration(text: str) -> list[dict]:
    """保密期限过长（>5年）。"""
    risks: list[dict] = []
    pattern = re.compile(
        r"保密(?:义务|期限|责任|期)?[^。\n；;]{0,60}?(\d+)\s*年",
    )
    for m in pattern.finditer(text):
        years = int(m.group(1))
        if years > 5:
            risks.append({
                "type": "保密期限过长",
                "severity": "medium",
                "description": (
                    f"检测到保密期限 {years} 年，超过 5 年的常见合理范围，"
                    "可能过度限制己方后续业务开展，建议协商缩短或明确保密信息范围。"
                ),
            })
    return risks


def _check_auto_renewal(text: str) -> list[dict]:
    """自动续约条款。"""
    risks: list[dict] = []
    patterns = [
        r"自动\s*(?:续约|续期|续签|延长|延展)",
        r"到期\s*后\s*(?:自动|默认)\s*(?:续约|续期|延长)",
        r"除非\s*(?:一方|双方)\s*(?:提前|书面)[^。\n；;]{0,30}?(?:不续约|不续期|不再续|终止)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            risks.append({
                "type": "自动续约条款",
                "severity": "low",
                "description": (
                    f"检测到「{m.group()}」条款，合同到期后可能自动续约，"
                    "需确认是否需要在到期前主动发出书面终止通知，避免被动续约。"
                ),
            })
    return risks


# ---------------------------------------------------------------------------
# 规则注册表（按检测顺序执行）
# ---------------------------------------------------------------------------

_RULES = [
    _check_penalty,
    _check_unilateral_termination,
    _check_ip_transfer,
    _check_jurisdiction,
    _check_confidentiality_duration,
    _check_auto_renewal,
]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def scan_contract(file_path: str) -> dict:
    """扫描合同文件，识别风险条款。

    基于关键词正则匹配，不依赖 LLM。支持 .txt / .md 等纯文本合同，
    优先以 UTF-8 读取，失败时回退 GBK。

    Args:
        file_path: 合同文件路径

    Returns:
        dict: {
            "file": path,
            "risks": [{"type", "severity", "description"}, ...],
            "summary": "共N个风险点",
        }
    """
    path = Path(file_path)

    if not path.exists():
        return {
            "file": file_path,
            "risks": [{
                "type": "文件不存在",
                "severity": "high",
                "description": f"指定的合同文件不存在：{file_path}",
            }],
            "summary": "共1个风险点",
        }

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gbk", errors="ignore")

    all_risks: list[dict] = []
    for rule in _RULES:
        all_risks.extend(rule(text))

    return {
        "file": file_path,
        "risks": all_risks,
        "summary": f"共{len(all_risks)}个风险点",
    }


# ---------------------------------------------------------------------------
# 独立运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        result = scan_contract(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("用法: python skills/handlers/legal_scan.py <合同文件路径>")
        sys.exit(1)
