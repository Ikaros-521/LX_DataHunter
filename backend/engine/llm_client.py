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
