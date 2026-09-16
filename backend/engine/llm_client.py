"""OpenAI 兼容的大模型客户端 + 视频分析 prompt 模板。

用 httpx 直连 {base_url}/chat/completions，避免引入 openai SDK 重依赖。
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from backend.config import settings

logger = logging.getLogger("lx_datahunter.llm")


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


def build_market_overview_prompt(competitors_summary: str) -> tuple[str, str]:
    """竞品调研：跨对象市场概览 (system, user) prompt（账号/关键词赛道通用）。"""
    system = (
        "你是资深市场竞争分析师，擅长基于多个账号或关键词赛道的公开运营数据"
        "与评论区反馈，评估内容策略、用户口碑与市场位置。只基于给定素材，"
        "不要编造未提及的信息。"
    )
    user = (
        f"以下是若干调研对象的摘要（竞品账号或关键词赛道）：\n\n{competitors_summary}\n\n"
        "请输出跨对象的市场对比分析（用 markdown，严格按下列分节）：\n"
        "## 市场格局\n概述这个赛道的内容生态：主要玩家、体量差异、内容形态。\n\n"
        "## 各家优势与短板\n用列表逐个对象说明其内容优势与明显短板（有评论区证据时注明）。\n\n"
        "## 差异化机会\n指出尚未被充分覆盖的内容方向、人群或表达形式（3 条以内）。\n\n"
        "## 行动建议\n给出可立即执行的跟进建议（3 条以内）。\n\n"
        "要求：语言精炼、结论先行；引用数据时保留原数字。"
    )
    return system, user


def build_keyword_content_prompt(keyword_desc: str, videos_table: str,
                                 top_authors: str = "") -> tuple[str, str]:
    """竞品调研：关键词赛道内容格局分析 (system, user) prompt。

    keyword_desc 为关键词与平台概况；videos_table 为该关键词下头部视频
    元数据文本行；top_authors 为按视频聚合的活跃作者榜文本（可选）。
    """
    system = (
        "你是资深内容市场分析师。基于某关键词在平台搜索结果中的头部视频"
        "元数据（标题/作者/播放/点赞/评论/发布时间，不含视频内容本身），"
        "分析该赛道的内容格局与竞争态势，并明确标注哪些是推断。"
    )
    user = (
        f"关键词概况：\n{keyword_desc}\n\n"
        f"该关键词下播放量靠前的视频（标题 | 作者 | 发布日期 | 播放 | 点赞 | 评论）：\n"
        f"{videos_table}\n"
        + (f"\n活跃作者榜（该关键词头部视频中的账号）：\n{top_authors}\n" if top_authors else "")
        + "\n请分析该关键词赛道（用 markdown，严格按下列分节）：\n"
        "## 赛道概况\n这个关键词下的内容主要分几类（教程/测评/案例/营销等），"
        "整体体量与热度如何。\n\n"
        "## 主要玩家\n谁在这个赛道里活跃：账号类型（个人/机构/品牌号）、"
        "大致体量、各自打法差异。\n\n"
        "## 爆款特征\n高播放视频的共性：选题角度、标题钩子、发布时间集中度。\n\n"
        "## 内容空白与机会\n哪些角度/人群/形式未被充分覆盖（3 条以内）。\n\n"
        "## 对我方的建议\n3 条以内可执行结论（切入点、差异化、避坑）。\n\n"
        "要求：结论基于数据，不臆造视频画面内容。"
    )
    return system, user


def build_competitor_content_prompt(creator_desc: str, videos_table: str) -> tuple[str, str]:
    """竞品调研：单竞品内容策略分析 (system, user) prompt。

    creator_desc 为账号概况文本；videos_table 为近期视频元数据
    （标题/发布时间/播放/点赞/评论/分享）的文本行列表。
    """
    system = (
        "你是资深短视频内容策略分析师。基于账号公开信息与近期视频的"
        "标题及互动数据（不含视频内容本身），推断其内容策略与运营水平，"
        "并明确标注哪些是推断。"
    )
    user = (
        f"账号概况：\n{creator_desc}\n\n"
        f"近期视频列表（标题 | 发布时间 | 播放 | 点赞 | 评论 | 分享 | 收藏 | 标签）：\n"
        f"{videos_table}\n\n"
        "请分析该账号（用 markdown，严格按下列分节）：\n"
        "## 内容方向与定位\n主打什么主题/人群/场景，内容组合如何。\n\n"
        "## 发布节奏与产量\n按发布时间推断更新频率、稳定性和发布时间偏好。\n\n"
        "## 爆款特征\n指出互动量最高的 1-3 条视频，从标题/标签归纳其共性钩子。\n\n"
        "## 制作与运营水平\n按互动率（点赞/播放等可算则算）评估内容质量、"
        "商业化痕迹（广告/带货信号）与运营手法。\n\n"
        "## 值得关注\n3 条以内：对手做得好、值得借鉴或需要警惕的点。\n\n"
        "要求：结论基于数据，不臆造视频画面内容。"
    )
    return system, user


def build_comment_insight_prompt(creator_desc: str, comments_text: str) -> tuple[str, str]:
    """竞品调研：单竞品评论区洞察 (system, user) prompt。

    comments_text 为按点赞排序的评论文本行（含点赞数与 IP 属地）。
    """
    system = (
        "你是资深用户研究分析师，擅长从视频评论区提炼用户画像、口碑与"
        "真实需求。只基于给定评论，不要编造。"
    )
    user = (
        f"账号概况：\n{creator_desc}\n\n"
        f"该账号爆款视频评论区的高赞评论（点赞数 | IP属地 | 内容）：\n"
        f"{comments_text}\n\n"
        "请输出评论区洞察（用 markdown，严格按下列分节）：\n"
        "## 用户画像\n评论者的典型人群、关注点与语气。\n\n"
        "## 口碑倾向\n好评集中在哪（产品/内容/服务），差评或质疑集中在哪；"
        "尽量给出大致比例感受。\n\n"
        "## 用户痛点与需求信号\n列出评论区反复出现的痛点、愿望、功能请求或"
        "购买意向（有原文则短引佐证）。\n\n"
        "## 对我方的启示\n3 条以内可执行结论（如可切入的内容点、可回应的质疑）。\n\n"
        "要求：引用评论原文时保持简短；无法判断的写「信息不足」。"
    )
    return system, user


def build_video_filter_prompt(focus: str,
                              titles: list[tuple[int, str, str]]) -> tuple[str, str]:
    """调研前视频相关性筛选 (system, user) prompt。

    focus 为调研主题（关键词模式=赛道关键词；账号模式=竞品备注名）；
    titles 为 [(编号, 视频标题, 作者昵称)]。要求 LLM 返回 {"keep": [...]} JSON，
    仅保留与主题相关的视频，剔除蹭词/无关内容。
    """
    system = (
        "你是资深内容运营，负责判断视频与调研主题的相关性。判断标准：\n"
        "1. 视频主体内容应与主题同品类、同场景或面向同类受众；\n"
        "2. 仅标题蹭到关键词但内容方向不同的（蹭词）应剔除；\n"
        "3. 与主题无关的纯娱乐、八卦、生活记录、其他品类内容应剔除；\n"
        "4. 标题信息不足以判断时保留（宁可多留，交给后续深度分析甄别）；\n"
        "5. 作者昵称通常反映账号定位，可作为辅助信号。\n"
        "先逐条思考每条视频与主题的关系，再给结论；最终只输出 JSON。"
    )
    lines = "\n".join(
        f"{idx} | {title[:60] or '(无标题)'} | {author[:30]}" for idx, title, author in titles)
    user = (
        f"调研主题：{focus}\n\n"
        f"候选视频（编号 | 标题 | 作者）：\n{lines}\n\n"
        "判定示例（主题=AI剪辑工具赛道）：\n"
        "- 保留「3款AI剪辑软件实测，新手也能剪出电影感」：直接评测主题品类；\n"
        "- 剔除「AI帮我写的辞职信火了」：仅蹭AI热词，与剪辑工具无关。\n\n"
        '请返回 JSON：{"keep": [相关视频的编号...], '
        '"drop_reasons": {"被剔除编号": "一句话原因", ...}}\n'
        "keep 必须涵盖所有你判断为相关的编号；只输出该 JSON。"
    )
    return system, user


def parse_keep_indexes(text: str, valid: set[int]) -> set[int] | None:
    """解析 LLM 筛选返回的 {"keep": [...]}，容错提取 JSON。

    返回保留编号集合（与 valid 求交后的合法子集）；
    解析失败或 keep 缺失返回 None（调用方应视为筛选失败、保留全部）。
    """
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    keep = obj.get("keep") if isinstance(obj, dict) else None
    if not isinstance(keep, list):
        return None
    result: set[int] = set()
    for i in keep:
        try:
            result.add(int(i))
        except (TypeError, ValueError):
            continue  # 混入的非法编号跳过
    return result & valid


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
            raise RuntimeError("LLM 未配置：请设置 LLM_BASE_URL 与 LLM_API_KEY")
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

    async def chat_messages(self, messages: list[dict]) -> str:
        """多轮对话：messages 为标准 OpenAI 消息数组（含 system/user/assistant）。

        与 ``chat`` 共用同一套 httpx 调用，区别仅在 messages 由调用方完全掌控，
        以便保留多轮问答历史。temperature 略高以利于解释性回答。
        """
        if not (self.base_url and self.api_key):
            raise RuntimeError("LLM 未配置：请设置 LLM_BASE_URL 与 LLM_API_KEY")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.5,
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

    async def chat_messages_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式多轮对话：逐 token ``yield`` content。

        与 ``chat_messages`` 语义一致，区别在 payload 加 ``stream=True``，
        用 ``client.stream`` 拿到 SSE 行流，按 OpenAI 格式解析每个 chunk 的
        ``delta.content``。connect 超时保留默认，read 超时设为 None 避免长回答
        被中途切断。
        """
        if not (self.base_url and self.api_key):
            raise RuntimeError("LLM 未配置：请设置 LLM_BASE_URL 与 LLM_API_KEY")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.5,
            "stream": True,
        }
        # 连接建立阶段保留超时，但流式读取阶段不设 read timeout
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions",
                headers=headers, json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content")
                    if token:
                        yield token
