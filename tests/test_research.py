"""竞品调研模块纯函数单测：评论解析 / 搜索解析 / uid 解析 / 报告拼装。"""
from backend.engine.comment_crawler import (
    parse_bilibili_reply, parse_douyin_comment_payload,
)
from backend.engine.competitor_discovery import (
    parse_bilibili_search_videos, parse_douyin_search_payload,
)
from backend.api.competitors import parse_platform_uid
from backend.engine.report_builder import (
    _fmt_int, build_keyword_report, build_report, sanitize_report_title,
)


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
                    "create_time": 1788699600,
                    "statistics": {"play_count": 250000},
                    "author": {"sec_uid": "SEC1", "nickname": "竞品A",
                               "signature": "专注剪辑", "follower_count": 120000},
                }},
                {"desc": "裸结构(无 aweme_info 层)",
                 "create_time": 1788600000,
                 "author": {"sec_uid": "SEC2", "nickname": "竞品B"}},
            ]},
        ],
        "status_code": 0,
    }
    hits = parse_douyin_search_payload(payload)
    assert len(hits) == 2
    assert hits[0]["sec_uid"] == "SEC1"
    assert hits[0]["follower_count"] == 120000
    assert hits[0]["create_time"] == 1788699600
    assert hits[0]["plays"] == 250000
    assert hits[1]["sec_uid"] == "SEC2"
    assert hits[1]["plays"] == 0  # 无 statistics 时默认 0


def test_parse_douyin_search_payload_empty():
    assert parse_douyin_search_payload({"data": []}) == []
    assert parse_douyin_search_payload({}) == []


def test_parse_bilibili_search_videos():
    data = {
        "result": [
            {
                "aid": 113700000001,
                "mid": 946974,
                "author": "影视飓风",
                "title": '如何用<em class="keyword">AI剪辑</em>提升效率',
                "arcurl": "http://www.bilibili.com/video/av113700000001",
                "play": 250000, "review": 1200, "like": 18000,
                "pubdate": 1788699600,
            },
            {"mid": 1},  # 无 aid 的脏数据应被跳过
        ],
    }
    videos = parse_bilibili_search_videos(data)
    assert len(videos) == 1
    v = videos[0]
    assert v["content_id"] == "113700000001"
    assert v["title"] == "如何用AI剪辑提升效率"  # 高亮标签已剥离
    assert v["author_uid"] == "946974"
    assert v["plays"] == 250000
    assert v["comments"] == 1200
    assert v["pub_ts"] == 1788699600
    assert v["url"].startswith("http://www.bilibili.com/video/av")


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


def _sample_keyword_section(index=1):
    return {
        "index": index,
        "keyword": "AI剪辑",
        "platform": "bilibili",
        "videos": [{
            "title": "爆款剪辑教程", "url": "https://www.bilibili.com/video/av1",
            "create_time": "2026-09-10", "plays": 250000, "likes": 18000,
            "comments": 1200, "shares": 0, "collects": 0, "tags": [],
            "content_id": "1", "author_nickname": "科技阁",
            "author_uid": "96434081", "author_homepage": "https://space.bilibili.com/96434081",
        }],
        "video_count": 1,
        "avg_plays": 250000, "avg_likes": 18000, "avg_comments": 1200,
        "publish_span": "2026-09-10 ~ 2026-09-10",
        "top_authors": [{
            "uid": "96434081", "nickname": "科技阁",
            "homepage": "https://space.bilibili.com/96434081",
            "video_hits": 1, "total_plays": 250000,
        }],
        "comment_count": 2,
        "search_screenshot": "assets/search_1.png",
        "video_screenshots": [{
            "title": "爆款剪辑教程", "url": "https://www.bilibili.com/video/av1",
            "screenshot": "assets/kw1_video_1.png",
        }],
        "top_comments": [{
            "user_nickname": "用户A", "text": "求教程", "likes": 99,
            "ip_location": "北京", "create_time": "2026-09-11",
        }],
        "content_analysis": "## 赛道概况\n测试",
        "comment_insight": "## 口碑倾向\n好评",
        "warnings": ["抖音未配置 Cookie"],
    }


def test_build_keyword_report_structure():
    md = build_keyword_report(
        title="AI 赛道调研",
        generated_at="2026-09-16 09:00",
        params={"max_videos_per_keyword": 15, "max_search_pages": 2,
                "top_videos_for_comments": 5, "max_comments_per_video": 50},
        sections=[_sample_keyword_section(1)],
        market_overview="## 市场格局\n测试概览",
        trigger_type="manual",
    )
    assert "# 关键词赛道调研报告：AI 赛道调研" in md
    assert "## 一、市场概览" in md and "测试概览" in md
    assert "## 二、赛道总览" in md
    assert '### 1. 关键词「AI剪辑」（B站）' in md
    assert "#### 头部内容" in md
    assert "[科技阁](https://space.bilibili.com/96434081)" in md
    assert "#### 活跃作者榜" in md
    assert "#### 赛道内容分析" in md and "测试" in md
    assert "#### 用户评论洞察（抓取 2 条评论）" in md
    assert "![「AI剪辑」搜索结果](assets/search_1.png)" in md
    assert "25.0万" in md
    assert "手动触发" in md


def test_build_keyword_report_empty():
    s = _sample_keyword_section()
    s["videos"] = []
    s["video_count"] = 0
    s["top_authors"] = []
    md = build_keyword_report("空", "2026-09-16", {}, [s])
    assert "未搜索到视频" in md
    assert "#### 活跃作者榜" not in md  # 空作者榜不输出该章节（数据说明文案除外）


def test_sanitize_report_title():
    assert sanitize_report_title("a/b\\c:d*e?f\"g<h>i|j k") == "a_b_c_d_e_f_g_h_i_j_k"
    assert sanitize_report_title("") == "竞品调研"
    assert sanitize_report_title("   ") == "竞品调研"


# ---- 评论抓取范围参数（-1 全部 / 0 未爬）----

def test_comment_scope_text():
    from backend.engine.report_builder import _comment_scope_text
    assert _comment_scope_text({}) == "全部视频的评论区各最多 100 条"
    assert _comment_scope_text(
        {"top_videos_for_comments": -1, "max_comments_per_video": -1}
    ) == "全部视频的评论区全量抓取"
    assert _comment_scope_text(
        {"top_videos_for_comments": 5, "max_comments_per_video": 50}
    ) == "Top 5 条视频的评论区各最多 50 条"
    assert _comment_scope_text({"top_videos_for_comments": 0}) == "未爬取评论"


# ---- B站评论翻页（限量 / 全量）----

def _fake_reply(rpid: int) -> dict:
    return {
        "rpid": rpid,
        "like": rpid,
        "ctime": 1726000000,
        "content": {"message": f"评论{rpid}"},
        "member": {"uname": f"用户{rpid}", "mid": str(rpid)},
    }


def _patch_bilibili_pages(monkeypatch, pages: list[dict], calls: list):
    """把 bilibili_api.comment.get_comments / sync 换成同步假实现。

    pages 为每页返回体；越界返回空 replies。calls 记录请求过的页码。
    """
    import bilibili_api
    import bilibili_api.comment as bili_comment

    def fake_get_comments(**kwargs):
        calls.append(kwargs.get("page_index"))
        idx = (kwargs.get("page_index") or 1) - 1
        return pages[idx] if idx < len(pages) else {"replies": None, "page": {"count": len(pages)}}

    monkeypatch.setattr(bili_comment, "get_comments", fake_get_comments)
    monkeypatch.setattr(bilibili_api, "sync", lambda value: value)


def test_crawl_bilibili_comments_unlimited(monkeypatch):
    import asyncio
    import backend.engine.comment_crawler as cc

    pages = [
        {"replies": [_fake_reply(i) for i in range(20)], "page": {"count": 2}},
        {"replies": [_fake_reply(i) for i in range(20, 25)], "page": {"count": 2}},
    ]
    calls: list[int] = []
    _patch_bilibili_pages(monkeypatch, pages, calls)

    items = asyncio.run(cc._crawl_bilibili_comments_legacy(123, 0))
    assert len(items) == 25  # 两页全取，不做截断
    assert calls == [1, 2, 3]  # 第三页空 replies 自然终止


def test_crawl_bilibili_comments_limited(monkeypatch):
    import asyncio
    import backend.engine.comment_crawler as cc

    pages = [
        {"replies": [_fake_reply(i) for i in range(20)], "page": {"count": 3}},
        {"replies": [_fake_reply(i) for i in range(20, 30)], "page": {"count": 3}},
    ]
    calls: list[int] = []
    _patch_bilibili_pages(monkeypatch, pages, calls)

    items = asyncio.run(cc._crawl_bilibili_comments_legacy(123, 25))
    assert len(items) == 25
    assert calls == [1, 2]  # 25 条两页取满，不再翻第三页


# ---- B站评论 main 游标接口 ----

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """按顺序吐出预设页面的假 httpx.AsyncClient。"""
    pages: list[dict] = []

    def __init__(self, *args, **kwargs):
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        page = self.pages[min(self._i, len(self.pages) - 1)]
        self._i += 1
        return _FakeResp(page)


def _main_page(rpids, next_, is_end, top=()):
    return {"code": 0, "data": {
        "replies": [_fake_reply(i) for i in rpids],
        "top_replies": [_fake_reply(i) for i in top],
        "cursor": {"next": next_, "is_end": is_end, "all_count": 7028},
    }}


def test_crawl_bilibili_comments_main_api(monkeypatch):
    import asyncio
    import httpx
    import backend.engine.comment_crawler as cc

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    _FakeAsyncClient.pages = [
        _main_page(range(30), next_=2, is_end=False, top=(999,)),   # 首页含置顶
        _main_page(range(30, 35), next_=3, is_end=True),
    ]
    unlimited = asyncio.run(cc.crawl_bilibili_comments(1, 0))
    assert len(unlimited) == 36  # 30 + 置顶1 + 5，含全部

    _FakeAsyncClient.pages = [_main_page(range(30), next_=2, is_end=False, top=(999,))]
    limited = asyncio.run(cc.crawl_bilibili_comments(1, 10))
    assert len(limited) == 10


def test_crawl_bilibili_comments_main_api_fallback(monkeypatch):
    """主接口拿不到数据时回退 legacy 路径。"""
    import asyncio
    import httpx
    import backend.engine.comment_crawler as cc

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.pages = [{"code": -412, "message": "blocked"}]

    legacy_pages = [{"replies": [_fake_reply(i) for i in range(5)], "page": {"count": 1}}]
    calls: list[int] = []
    _patch_bilibili_pages(monkeypatch, legacy_pages, calls)

    items = asyncio.run(cc.crawl_bilibili_comments(1, 0))
    assert len(items) == 5  # legacy 兜底返回


# ---- B站弹幕解析 / 抓取（限量与全量）----

def _fake_sdk_danmaku(i: int):
    from types import SimpleNamespace
    return SimpleNamespace(
        id_=i, id_str="", text=f" 弹幕{i} ", send_time=1726000000.0,
        dm_time=float(i), crc32_id=f"hash{i}", color="#ffffff",
        mode=4, font_size=18, pool=0, weight=6,
    )


def test_parse_bilibili_danmaku():
    from backend.engine.comment_crawler import parse_bilibili_danmaku
    item = parse_bilibili_danmaku(_fake_sdk_danmaku(42))
    assert item.danmaku_id == "42"
    assert item.text == "弹幕42"
    assert item.send_time is not None
    assert item.dm_time == 42.0
    assert item.mode == 4
    assert item.font_size == 18
    assert item.color == "ffffff"
    assert item.user_hash == "hash42"
    assert item.extra["weight"] == 6


def test_crawl_bilibili_danmakus(monkeypatch):
    import asyncio
    import bilibili_api.video as bv
    import backend.engine.comment_crawler as cc

    raw = [_fake_sdk_danmaku(i) for i in range(5)] + [_fake_sdk_danmaku(3)]  # 含重复 id

    async def fake_get_danmakus(self, **kwargs):
        return raw

    monkeypatch.setattr(bv.Video, "get_danmakus", fake_get_danmakus)
    assert len(asyncio.run(cc.crawl_bilibili_danmakus(1, 0))) == 5   # 全量 + 去重
    assert len(asyncio.run(cc.crawl_bilibili_danmakus(1, 3))) == 3   # 限量截断


def test_report_danmaku_count_line():
    s = _sample_section()
    s["danmaku_count"] = 1234
    md = build_report("弹幕报告", "2026-09-16", {}, [s], trigger_type="manual")
    assert "原始弹幕存库 **1234** 条" in md

    kw = _sample_keyword_section()
    kw["danmaku_count"] = 88
    md2 = build_keyword_report("弹幕赛道", "2026-09-16", {}, [kw], trigger_type="manual")
    assert "原始弹幕存库 **88** 条" in md2


# ---- 弹幕查询/导出 API（内存库直连）----

def test_research_danmakus_list_and_export():
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.models import Base, ResearchTask, ResearchDanmaku
    from backend.api.research import export_research_danmakus, list_research_danmakus

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            task = ResearchTask(title="弹幕任务", competitor_stats=[{
                "videos": [{"content_id": "111", "title": "视频甲"},
                           {"content_id": "222", "title": "视频乙"}]}])
            db.add(task)
            await db.flush()
            for cid in ("111", "222"):
                for i in range(3):
                    db.add(ResearchDanmaku(
                        research_task_id=task.id, competitor_id=1, content_id=cid,
                        danmaku_id=f"{cid}-{i}", text=f"弹幕{i}", dm_time=float(i),
                        mode=4, font_size=18, color="ffffff", user_hash="h"))
            await db.commit()

            resp = await list_research_danmakus(task.id, db=db)
            assert resp["total"] == 6
            assert {c.count for c in resp["contents"]} == {3}
            assert {c.content_id: c.title for c in resp["contents"]}["111"] == "视频甲"

            filtered = await list_research_danmakus(task.id, content_id="222", db=db)
            assert filtered["total"] == 3
            assert all(i.content_id == "222" for i in filtered["items"])
            assert len(filtered["contents"]) == 2  # 统计不受筛选影响

            csv_resp = await export_research_danmakus(task.id, db=db)
            body = csv_resp.body.decode("utf-8")
            assert csv_resp.media_type.startswith("text/csv")
            assert "弹幕内容" in body and "视频甲" in body and "底部" in body
        await eng.dispose()

    asyncio.run(run())
