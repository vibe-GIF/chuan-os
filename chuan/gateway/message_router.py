"""① Message Router —— 意图解析与路由（显式锁定＞关键词＞LLM＞本体兜底）。

职责：关键词/LLM 两段路由决策 + 确定性天气兜底预处理。
从 RuntimeSupervisor 迁移而来（ADR-012 Gateway 拆分）。

免费模型（glm-4-flash / cloud_general）的 tool-calling 是概率性的，
凡命中天气意图就预取实况并注入用户消息，让答案永远基于真实数据。
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor


class MessageRouter:
    """意图解析与路由决策。只做「决定去哪儿」，不执行分发。"""

    _WEATHER_TRIGGERS = ("天气", "气温", "温度", "湿度", "预报", "几度", "下雨", "降水量")

    # Whisper 中文转写常输出繁体，导致关键词路由和天气兜底匹配不上。
    # 在 dispatch 入口统一归一化为简体（未命中字符原样保留）。
    _T2S = str.maketrans(
        "漢氣溫濕預幾廣東陽陰蘇寧滬慶陝龍遼烏蘭銀鄭濟長貴無錫廈門邊連島莊頭亞魯齊薩臺雞鶴馬開運樣問嗎這裡麼說對從後號機個們來時"
        "話語讀書學體風飛雲電雙條魚貝閃間際隨難壓廠應優傳傷傾備債兒兌區醫華協單賣歷歸當錄徹徵憶態懷擾擊搖損換敵數舊談誰調請諸諾講謝謠譯議護負財貢館駐騰驚骨",
        "汉气温湿预几广东阳阴苏宁沪庆陕龙辽乌兰银郑济长贵无锡厦门边连岛庄头亚鲁齐萨台鸡鹤马开运样问吗这里么说对从后号机个们来时"
        "话语读书学体风飞云电双条鱼贝闪间际随难压厂应优传伤倾备债儿兑区医华协单卖历归当录彻征忆态怀扰击摇损换敌数旧谈谁调请诸诺讲谢谣译议护负财贡馆驻腾惊骨",
    )

    def __init__(self, sup: RuntimeSupervisor) -> None:
        self._sup = sup

    # ------------------------------------------------------------------ #
    # 路由决策
    # ------------------------------------------------------------------ #
    def preview(self, message: str) -> str | None:
        """关键词路由预览（纯本地匹配，不调 LLM）。返回岗位名或 None。"""
        sup = self._sup
        if not sup._is_awake or sup._orchestrator is None:
            return None
        target = sup._orchestrator.route(self.simplify(message))
        if target and target in sup._workers:
            return target
        return None

    def route_with_llm(
        self, message: str, history: list[dict[str, str]] | None = None
    ) -> str | None:
        """用幕僚长大脑 LLM 从可用岗位中选一个最合适的；失败返回 None。"""
        sup = self._sup
        chief_persona = sup._persona_loader.get_persona("chief_of_staff")
        if chief_persona:
            chief_brain = sup.brains.get(chief_persona.brain)
        else:
            chief_brain = sup.brains.default()

        if chief_brain is None:
            return None

        worker_names = sorted(sup._workers.keys())
        system_prompt = (
            "You are the chief of staff of chuan-os. Route the user to the right specialist.\n"
            f"Available specialists: {', '.join(worker_names)}\n"
            "Reply with ONLY the specialist name (exactly one from the list), nothing else. "
            "No explanation, no quotes, no punctuation."
        )

        messages: list[dict[str, str]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        try:
            response = chief_brain.complete(messages, system=system_prompt)
        except Exception:  # noqa: BLE001 - LLM 不可用时降级
            return None

        name = response.strip().lower().strip("`*\"' .")
        if name in sup._workers:
            return name
        for w in worker_names:
            if w in name or name in w:
                return w
        return None

    # ------------------------------------------------------------------ #
    # 繁体→简体 与 天气兜底
    # ------------------------------------------------------------------ #
    @classmethod
    def simplify(cls, text: str) -> str:
        """繁体→简体归一化（仅覆盖常用字，未命中字符原样保留）。"""
        return text.translate(cls._T2S) if text else text

    @classmethod
    def is_weather_intent(cls, message: str) -> bool:
        return any(t in message for t in cls._WEATHER_TRIGGERS)

    @staticmethod
    def extract_city(message: str) -> str | None:
        """从问句中启发式抽取城市名；抽不出返回 None（交由正常流程）。"""
        m = message
        for w in (
            "天气", "气温", "温度", "湿度", "预报", "几度", "下雨", "降水量",
            "未来几天", "未来一周", "最近", "这几天", "多少度", "多少",
            "怎么样", "怎样", "如何", "咋样",
        ):
            m = m.replace(w, "")
        m = re.sub(r"(今天|明天|昨天|后天|前天|现在|目前|今明|一周|三天|两天|十五天|\d+号|这周|下周)", "", m)
        m = re.sub(r"[从到至定的问题假你请问吗?？。！!、，,\s]", "", m)
        m = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", m)
        return m or None

    def ground_weather(self, message: str) -> str:
        """命中天气意图且能抽出城市时，把实况注入用户消息。"""
        sup = self._sup
        if not self.is_weather_intent(message):
            return message
        city = self.extract_city(message)
        if not city:
            return message
        weather_text = self._fetch_weather_text(city)
        if not weather_text:
            return message
        return (
            f"[天气实事] {weather_text}\n"
            f"用户原话：{message}\n"
            f"请直接依据上述天气实事如实回答用户，务必包含天气、温度、湿度，不要再次调用天气工具。"
        )

    def _fetch_weather_text(self, city: str) -> str | None:
        """在常驻事件循环上调用 get_weather，返回实况文本；失败返回 None。"""
        sup = self._sup
        wt = None
        for t in sup.mcp_adapter.get_tools("weather"):
            if t.name == "get_weather":
                wt = t
                break
        if wt is None:
            return None
        from concurrent.futures import Future

        future: Future = asyncio.run_coroutine_threadsafe(
            wt.ainvoke({"city": city}), sup._loop
        )
        try:
            text = str(future.result(timeout=30))
        except Exception:  # noqa: BLE001 - 天气失败则回退正常流程
            return None
        if any(bad in text for bad in ("错误", "失败", "超时")):
            return None
        return text