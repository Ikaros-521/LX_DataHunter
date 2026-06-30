"""抖音下载纯函数的单测（不涉及浏览器/网络）。"""
import pytest

from backend.engine.downloader import DownloadEngine


# ---- _pick_douyin_video_url ----

def test_pick_video_url_prefers_play_addr():
    detail = {"video": {"play_addr": {"url_list": ["https://x/playwm/a.mp4"]}}}
    assert DownloadEngine._pick_douyin_video_url(detail) == "https://x/playwm/a.mp4"


def test_pick_video_url_falls_back_to_play_addr_h264():
    detail = {"video": {
        "play_addr": {"url_list": []},
        "play_addr_h264": {"url_list": ["https://x/h264.mp4"]},
    }}
    assert DownloadEngine._pick_douyin_video_url(detail) == "https://x/h264.mp4"


def test_pick_video_url_falls_back_to_bit_rate():
    detail = {"video": {"bit_rate": [
        {"play_addr": {"url_list": []}},
        {"play_addr": {"url_list": ["https://x/br.mp4"]}},
    ]}}
    assert DownloadEngine._pick_douyin_video_url(detail) == "https://x/br.mp4"


@pytest.mark.parametrize("detail", [{}, {"video": {}}, {"video": {"play_addr": {}}}])
def test_pick_video_url_none_when_missing(detail):
    assert DownloadEngine._pick_douyin_video_url(detail) is None


# ---- _to_no_watermark ----

def test_to_no_watermark_replaces_playwm_once():
    url = "https://cdn.douyin.com/abc/playwm/def.mp4?token=playwm"
    # 只替换路径段第一个 /playwm/，不动 query
    assert DownloadEngine._to_no_watermark(url) == \
        "https://cdn.douyin.com/abc/play/def.mp4?token=playwm"


def test_to_no_watermark_passthrough_when_no_marker():
    url = "https://cdn.douyin.com/abc/play/def.mp4"
    assert DownloadEngine._to_no_watermark(url) == url
