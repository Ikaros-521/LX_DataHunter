"""竞品调研 Markdown 报告拼装（纯函数，便于单测）。

输入为 research_engine 准备好的结构化 dict，输出自包含 markdown 字符串：
截图引用相对路径 assets/xxx.png，视频以标题超链接呈现，前端渲染时把
assets/.. 改写为 /api/v1/research/{id}/assets/..。
"""
from __future__ import annotations

import re
from typing import Any

PLATFORM_DISPLAY = {"douyin": "抖音", "bilibili": "B站", "kuaishou": "快手", "xiaohongshu": "小红书"}


def _fmt_int(n: Any) -> str:
    """数字人性化：12345 -> 1.2万。"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _cell(text: Any, max_len: int = 60) -> str:
    """表格单元格安全化：去换行、转义竖线、截断。"""
    s = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    s = s.replace("|", "\\|")
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def _md_link(title: str, url: str, max_len: int = 60) -> str:
    t = _cell(title, max_len) or "(无标题)"
    if not url:
        return t
    # 链接地址里的括号会破坏 md 语法
    url = url.replace("(", "%28").replace(")", "%29")
    return f"[{t}]({url})"


def _video_table(videos: list[dict]) -> str:
    if not videos:
        return "*(该时间范围内未抓取到视频)*"
    header = ("| 发布时间 | 标题 | 播放 | 点赞 | 评论 | 分享 | 收藏 | 标签 |\n"
              "|---|---|---|---|---|---|---|---|")
    rows = []
    for v in videos:
        ct = (v.get("create_time") or "")[:10] or "-"
        tags = _cell("、".join(v.get("tags") or [])[:4], 30)
        rows.append(
            f"| {ct} | {_md_link(v.get('title', ''), v.get('url', ''))} "
            f"| {_fmt_int(v.get('plays'))} | {_fmt_int(v.get('likes'))} "
            f"| {_fmt_int(v.get('comments'))} | {_fmt_int(v.get('shares'))} "
            f"| {_fmt_int(v.get('collects'))} | {tags} |"
        )
    return header + "\n" + "\n".join(rows)


def _overview_table(sections: list[dict]) -> str:
    header = ("| # | 竞品 | 平台 | 粉丝 | 抓取视频数 | 平均点赞 | 平均评论 | 平均播放 |\n"
              "|---|---|---|---|---|---|---|---|")
    rows = []
    for s in sections:
        name = s.get("name") or s.get("nickname") or s.get("platform_uid", "?")
        rows.append(
            f"| {s.get('index', '')} | {_cell(name, 24)} | {PLATFORM_DISPLAY.get(s.get('platform'), s.get('platform'))} "
            f"| {_fmt_int(s.get('follower_count'))} | {s.get('video_count', 0)} "
            f"| {_fmt_int(s.get('avg_likes'))} | {_fmt_int(s.get('avg_comments'))} "
            f"| {_fmt_int(s.get('avg_plays'))} |"
        )
    return header + "\n" + "\n".join(rows)


def _comment_block(comments: list[dict], limit: int = 10) -> str:
    if not comments:
        return "*(未抓取到评论)*"
    lines = []
    for c in comments[:limit]:
        nick = _cell(c.get("user_nickname"), 20) or "匿名"
        meta = [f"👍 {_fmt_int(c.get('likes'))}"]
        if c.get("ip_location"):
            meta.append(str(c["ip_location"]))
        if c.get("create_time"):
            meta.append(str(c["create_time"])[:10])
        lines.append(f"> 💬 **@{nick}**（{' · '.join(meta)}）  \n> {_cell(c.get('text'), 160)}\n")
    return "\n".join(lines)


def _section_md(s: dict) -> str:
    """单个竞品详情章节。"""
    idx = s.get("index", 1)
    name = s.get("name") or s.get("nickname") or s.get("platform_uid", "?")
    platform = PLATFORM_DISPLAY.get(s.get("platform"), s.get("platform", ""))
    parts: list[str] = []
    parts.append(f"### {idx}. {name}\n")

    # 账号概况行
    meta_bits = [f"**平台**：{platform}"]
    if s.get("nickname"):
        meta_bits.append(f"**账号**：[{s['nickname']}]({s['homepage_url']})" if s.get("homepage_url") else f"**账号**：{s['nickname']}")
    if s.get("follower_count"):
        meta_bits.append(f"**粉丝**：{_fmt_int(s['follower_count'])}")
    if s.get("signature"):
        meta_bits.append(f"**签名**：{_cell(s['signature'], 80)}")
    parts.append(" | ".join(meta_bits) + "\n")

    # 主页截图
    if s.get("profile_screenshot"):
        parts.append(f"![{name} 主页]({s['profile_screenshot']})\n")

    # 数据摘要
    stats_bits = []
    if s.get("video_count"):
        stats_bits.append(f"抓取视频 **{s['video_count']}** 条")
        if s.get("publish_span"):
            stats_bits.append(f"发布跨度 {s['publish_span']}")
        stats_bits.append(f"平均点赞 **{_fmt_int(s.get('avg_likes'))}** / 平均评论 **{_fmt_int(s.get('avg_comments'))}**")
    if s.get("comment_count") is not None:
        stats_bits.append(f"抓取评论 **{s.get('comment_count', 0)}** 条")
    if stats_bits:
        parts.append("- " + "\n- ".join([f"数据摘要：{stats_bits[0]}"] + stats_bits[1:]) + "\n")

    # 近期内容表
    parts.append("#### 近期内容\n")
    parts.append(_video_table(s.get("videos") or []) + "\n")

    # 内容策略 LLM
    parts.append("#### 内容策略分析\n")
    parts.append((s.get("content_analysis") or "*（LLM 分析不可用或未启用）*") + "\n")

    # 评论区洞察 LLM
    parts.append(f"#### 评论区洞察（抓取 {s.get('comment_count', 0)} 条评论）\n")
    parts.append((s.get("comment_insight") or "*（评论数不足或 LLM 分析不可用）*") + "\n")

    # 代表性评论
    if s.get("top_comments"):
        parts.append("#### 代表性高赞评论\n")
        parts.append(_comment_block(s["top_comments"]) + "\n")

    # 爆款视频截图
    shots = s.get("video_screenshots") or []
    if shots:
        parts.append("#### 视频截图\n")
        for v in shots:
            title = _cell(v.get("title"), 40) or "视频"
            parts.append(f"**{title}**（{_md_link('查看原视频', v.get('url', ''))}）\n")
            parts.append(f"![{title}]({v.get('screenshot', '')})\n")

    for w in s.get("warnings") or []:
        # 错误信息可能含 URL 换行，压成单行避免破坏引用块
        parts.append(f"> ⚠️ {' '.join(str(w).split())}\n")
    return "\n".join(parts)


def build_report(
    title: str,
    generated_at: str,
    params: dict,
    sections: list[dict],
    market_overview: str = "",
    trigger_type: str = "manual",
) -> str:
    """拼装完整 Markdown 报告。"""
    trigger = "定时自动" if trigger_type == "scheduled" else "手动触发"
    date_range = ""
    if params.get("date_from") or params.get("date_to"):
        date_range = f"{params.get('date_from') or '开始'} ~ {params.get('date_to') or '今天'}"

    lines: list[str] = []
    lines.append(f"# 竞品调研报告：{title}\n")
    lines.append(
        f"> 生成时间：{generated_at}  \n"
        f"> 触发方式：{trigger}  \n"
        f"> 竞品数量：{len(sections)}  \n"
        f"> 视频时间范围：{date_range or '未限定'}  \n"
        f"> 调研参数：每竞品最多 {params.get('max_videos_per_competitor', 8)} 条视频，"
        f"Top {params.get('top_videos_for_comments', 5)} 条视频的评论区各 "
        f"{params.get('max_comments_per_video', 50)} 条评论\n"
    )

    lines.append("## 一、市场概览（LLM 跨竞品对比）\n")
    lines.append((market_overview or "*（LLM 分析不可用或未启用）*") + "\n")

    lines.append("## 二、竞品总览\n")
    lines.append(_overview_table(sections) + "\n")

    lines.append("## 三、竞品详情\n")
    for s in sections:
        lines.append(_section_md(s))

    all_warnings = [w for s in sections for w in (s.get("warnings") or [])]
    lines.append("## 四、数据说明\n")
    lines.append("- 数据来源：各平台公开页面/接口，互动数据为抓取时刻快照，可能与实时数据略有出入。")
    lines.append("- LLM 分析基于标题、互动数据与评论文本推断，仅供参考。")
    if all_warnings:
        lines.append("- 本次调研的跳过项：")
        for w in all_warnings:
            lines.append(f"  - {' '.join(str(w).split())}")
    return "\n".join(lines) + "\n"


def sanitize_report_title(title: str, fallback: str = "竞品调研") -> str:
    """报告目录名安全化。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (title or "").strip())
    return (s[:60] or fallback)
