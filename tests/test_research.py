"""竞品调研模块纯函数单测：评论解析 / 搜索解析 / uid 解析 / 报告拼装。"""
from backend.engine.comment_crawler import (
    parse_bilibili_reply, parse_douyin_comment_payload,
)
from backend.engine.competitor_discovery import parse_douyin_search_payload
from backend.api.competitors import parse_platform_uid
from backend.engine.report_builder import _fmt_int, build_report, sanitize_report_title


# ---- parse_platform_uid ----

def test_parse_douyin_homepage_link():
    uid, url = parse_platform_uid("douyin", "https://www.douyin.com/user/MS4wLjABAAAAabc123?x=1")
    assert uid == "MS4wLjABAAAAabc123"
    assert url.endswith("/user/MS4wLjABAAAAabc123")


def test_parse_douyin_raw_sec_uid():
    uid, _ = parse_platform_uid("douyin", "MS4wLjABAAAAxyz")
    assert uid == "MS4wLjABAAAAxyz"


def test_parse_bilibili_space_link():
    uid, url = parse_platform_uid("bilibili", "https://space.bilibili.com/2267573?from=search")
    assert uid == "2267573"
    assert url == "https://space.bilibili.com/2267573"


def test_parse_bilibili_raw_mid():
    uid, _ = parse_platform_uid("bilibili", "2267573")
    assert uid == "2267573"


def test_parse_invalid_bilibili_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_platform_uid("bilibili", "随便打的")


# ---- 抖音评论 XHR 解析 ----

def test_parse_douyin_comment_payload():
    payload = {
        "status_code": 0,
        "comments": [
            {
                "cid": "7411111",
                "text": "这个产品真的好用",
                "create_time": 1726000000,
                "digg_count": 523,
                "ip_label": "广东",
                "reply_comment_total": 12,
                "user": {"nickname": "小明"},
            },
            {"cid": "", "text": "", "user": {}},  # 空评论也应安全解析
        ],
        "has_more": 0,
    }
    items = parse_douyin_comment_payload(payload)
    assert len(items) == 2
    first = items[0]
    assert first.comment_id == "7411111"
    assert first.user_nickname == "小明"
    assert first.text == "这个产品真的好用"
    assert first.likes == 523
    assert first.ip_location == "广东"
    assert first.sub_comment_count == 12
    assert first.create_time is not None


# ---- B站评论解析 ----

def test_parse_bilibili_reply():
    reply = {
        "rpid": 123456,
        "like": 890,
        "ctime": 1726000000,
        "rcount": 5,
        "content": {"message": "前排出镜哈哈哈哈"},
        "member": {"uname": "测试用户", "mid": "42"},
        "reply_control": {"location": "IP属地：上海"},
    }
    c = parse_bilibili_reply(reply)
    assert c.comment_id == "123456"
    assert c.user_nickname == "测试用户"
    assert c.text == "前排出镜哈哈哈哈"
    assert c.likes == 890
    assert c.ip_location == "上海"
    assert c.sub_comment_count == 5
    assert c.extra["mid"] == "42"


# ---- 抖音搜索 XHR 解析 ----

def test_parse_douyin_search_payload():
    payload = {
        "data": [
            {"aweme_list": [
                {"aweme_info": {
                    "desc": "超好用的剪辑技巧",
                    "author": {"sec_uid": "SEC1", "nickname": "竞品A",
                               "signature": "专注剪辑", "follower_count": 120000},
                }},
                {"desc": "裸结构(无 aweme_info 层)",
                 "author": {"sec_uid": "SEC2", "nickname": "竞品B"}},
            ]},
        ],
        "status_code": 0,
    }
    hits = parse_douyin_search_payload(payload)
    assert len(hits) == 2
    assert hits[0]["sec_uid"] == "SEC1"
    assert hits[0]["follower_count"] == 120000
    assert hits[1]["sec_uid"] == "SEC2"


def test_parse_douyin_search_payload_empty():
    assert parse_douyin_search_payload({"data": []}) == []
    assert parse_douyin_search_payload({}) == []


# ---- 报告拼装 ----

def _sample_section(index=1):
    return {
        "index": index,
        "name": "竞品A",
        "platform": "douyin",
        "platform_uid": "SEC1",
        "homepage_url": "https://www.douyin.com/user/SEC1",
        "nickname": "竞品A官方",
        "signature": "官方账号",
        "follower_count": 123456,
        "videos": [{
            "title": "爆款视频 | 带竖线标题",
            "url": "https://www.douyin.com/video/123",
            "create_time": "2026-09-01",
            "plays": 100000, "likes": 5000, "comments": 300,
            "shares": 100, "collects": 200, "tags": ["测试"],
            "content_id": "123",
        }],
        "video_count": 1,
        "avg_likes": 5000, "avg_comments": 300, "avg_plays": 100000,
        "publish_span": "2026-09-01 ~ 2026-09-01",
        "comment_count": 2,
        "profile_screenshot": "assets/comp1_profile.png",
        "video_screenshots": [{
            "title": "爆款视频", "url": "https://www.douyin.com/video/123",
            "screenshot": "assets/comp1_video_1.png",
        }],
        "top_comments": [{
            "user_nickname": "小明", "text": "很好用", "likes": 523,
            "ip_location": "广东", "create_time": "2026-09-10",
        }],
        "content_analysis": "## 内容方向\n测试分析",
        "comment_insight": "## 口碑倾向\n好评居多",
        "warnings": ["抖音未配置登录 Cookie"],
    }


def test_fmt_int():
    assert _fmt_int(123456) == "12.3万"
    assert _fmt_int(999) == "999"
    assert _fmt_int(0) == "0"
    assert _fmt_int(None) == "0"


def test_build_report_structure():
    md = build_report(
        title="测试调研",
        generated_at="2026-09-15 12:00",
        params={"max_videos_per_competitor": 8, "top_videos_for_comments": 5,
                "max_comments_per_video": 50},
        sections=[_sample_section(1), _sample_section(2)],
        market_overview="## 市场格局\n两家竞品",
        trigger_type="manual",
    )
    # 主要章节齐全
    assert "# 竞品调研报告：测试调研" in md
    assert "## 一、市场概览" in md
    assert "两家竞品" in md
    assert "## 二、竞品总览" in md
    assert "## 三、竞品详情" in md
    assert "### 1. 竞品A" in md and "### 2. 竞品A" in md
    assert "## 四、数据说明" in md
    # 表格竖线转义 + 链接存在
    assert "带竖线标题" in md and "爆款视频 \\| 带竖线标题" in md
    assert "[爆款视频 \\| 带竖线标题](https://www.douyin.com/video/123)" in md
    # 截图相对路径
    assert "![竞品A 主页](assets/comp1_profile.png)" in md
    # 数字人性化
    assert "12.3万" in md
    # 警告进入数据说明
    assert "抖音未配置登录 Cookie" in md
    # 手动触发
    assert "手动触发" in md


def test_build_report_empty_videos():
    s = _sample_section()
    s["videos"] = []
    s["video_count"] = 0
    s["top_comments"] = []
    md = build_report("空", "2026-09-15 12:00", {}, [s])
    assert "未抓取到视频" in md
    assert "*（未抓取到评论）*" not in md  # top_comments 为空时不输出该节


def test_sanitize_report_title():
    assert sanitize_report_title("a/b\\c:d*e?f\"g<h>i|j k") == "a_b_c_d_e_f_g_h_i_j_k"
    assert sanitize_report_title("") == "竞品调研"
    assert sanitize_report_title("   ") == "竞品调研"
