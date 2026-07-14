"""OpenAI 兼容的大模型客户端 + 视频分析 prompt 模板。

用 httpx 直连 {base_url}/chat/completions，避免引入 openai SDK 重依赖。
"""
from __future__ import annotations

import logging

import httpx

from backend.config import settings

logger = logging.getLogger("fapai.llm")


def build_script_prompt(transcript: str) -> tuple[str, str]:
    """脚本结构 + 拍摄/制作手法分析的 (system, user) prompt。"""
    system = (
        "你是资深短视频编导与内容运营。基于视频的语音逐字稿，"
        "客观分析其脚本结构与拍摄/制作手法，输出结构化、可复用的结论。"
    )
    user = (
        "以下是视频的语音逐字稿：\n"
        f"{transcript}\n\n"
        "请从以下维度分析（用 markdown 小标题分节，每节给出要点）：\n"
        "1. 脚本结构：开头钩子、节奏、信息层次、卖点、引导互动(CTA)。\n"
        "2. 拍摄手法：镜头景别与运动、画面构图、出镜方式（口播/实拍/混剪）。\n"
        "3. 制作手法：剪辑节奏、字幕、音乐/音效、转场、包装元素。\n"
        "4. 可复用要点：3 条最值得借鉴的做法。\n"
        "只依据逐字稿能推断的内容，不要编造画面细节。"
    )
    return system, user


def build_summary_prompt(transcript: str) -> tuple[str, str]:
    """内容总结的 (system, user) prompt。"""
    system = "你是内容运营助手，擅长把视频内容凝练成简明摘要。"
    user = (
        "以下是视频的语音逐字稿：\n"
        f"{transcript}\n\n"
        "请总结这条视频（用 markdown）：先一句话概括主题，"
        "再列 3-5 个要点；如有明显目标人群或价值主张，单列一条。"
    )
    return system, user


def build_knowledge_prompt(transcript: str) -> tuple[str, str]:
    """视频知识总结：(system, user) prompt。

    面向课程/讲座/教程等"知识型"视频，把零散讲解提炼成结构化、可快速
    复习的知识点卡片。与内容总结的区别：内容总结偏运营视角（主题+要点），
    知识总结偏学习视角（概念/数据/术语/原理）。
    """
    system = (
        "你是资深知识整理师，擅长把视频讲解提炼成结构清晰、便于快速学习和"
        "复习的知识点卡片。只基于逐字稿可确定的内容，不编造未提及的信息。"
    )
    user = (
        "以下是视频的语音逐字稿：\n"
        f"{transcript}\n\n"
        "请提炼这条视频的知识点（用 markdown，严格按下列分节输出）：\n"
        "## 核心主题\n用一两句话讲清楚这条视频在讲什么、解决什么问题。\n\n"
        "## 关键知识点\n用编号列表列出视频中的核心知识点，每条先给名词/结论，"
        "再简要解释。保留逻辑先后顺序。\n\n"
        "## 重要数据与事实\n用列表列出视频中出现的具体数字、案例、引用、"
        "实验/研究结果等事实性信息（没有则写「无」）。\n\n"
        "## 术语表\n列出视频中出现的专业术语或易混淆概念及其解释，"
        "格式为「术语：解释」（没有则写「无」）。\n\n"
        "## 原理与流程\n如视频涉及原理推导、步骤流程、方法论，用编号步骤"
        "或逻辑链呈现；没有则写「无」。\n\n"
        "要求：保持客观、信息密度高、语言精炼；不要寒暄或重复逐字稿原文。"
    )
    return system, user


def build_digest_prompt(items: list[tuple[str, str]]) -> tuple[str, str]:
    """跨视频"大总结" prompt：(system, user)。

    items 为 [(视频标题, 该视频的单项总结文本), ...]，由调用方保证内容非空。
    输出面向"把多条视频放在一起快速学习/对比"的综合 markdown：
    主题归类 / 共性要点 / 差异与互补 / 综合结论。
    """
    system = (
        "你是资深知识整合分析师。你会拿到多条视频各自已有的总结，"
        "请把它们整合成一份跨视频的综合大总结，帮助读者快速掌握这一组"
        "视频的整体脉络与关键收获。只基于给定的素材，不要编造未提及的信息。"
    )
    parts = []
    for i, (title, text) in enumerate(items, 1):
        parts.append(f"### 视频{i}：{title or '(无标题)'}\n{text}")
    joined = "\n\n".join(parts)
    user = (
        f"以下是 {len(items)} 条视频各自的总结：\n\n"
        f"{joined}\n\n"
        "请输出一份跨视频的综合大总结（用 markdown，严格按下列分节）：\n"
        "## 主题概览\n一两句话说明这组视频共同在讲什么、覆盖了哪些方面。\n\n"
        "## 共性要点\n用编号列表归纳多条视频中反复出现、彼此印证的核心要点"
        "（尽量合并重复项）。\n\n"
        "## 差异与互补\n用列表指出各视频之间不同的观点、角度、案例或侧重，"
        "必要时标注来源视频编号（没有明显差异则写「无明显差异」）。\n\n"
        "## 综合结论\n给出整体性结论或可执行的收获（3 条以内）。\n\n"
        "要求：语言精炼、信息密度高；不要逐条复述每个视频的总结。"
    )
    return system, user


class LlmClient:
    """OpenAI 兼容 /chat/completions 客户端。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "LlmClient":
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout,
        )

    async def chat(self, system: str, user: str) -> str:
        if not (self.base_url and self.api_key):
            raise RuntimeError("LLM 未配置：请设置 FAPAI_LLM_BASE_URL 与 FAPAI_LLM_API_KEY")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
