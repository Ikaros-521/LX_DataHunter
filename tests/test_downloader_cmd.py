"""下载引擎 yt-dlp 命令构造的单元测试。

只测纯函数 ``DownloadEngine._build_ytdlp_cmd``，不涉及子进程/数据库/事件循环。
"""
import pytest

from backend.engine.downloader import DownloadEngine


def test_cmd_starts_with_ytdlp_and_ends_with_url():
    cmd = DownloadEngine._build_ytdlp_cmd(
        url="https://www.douyin.com/video/1",
        output_template="/tmp/out.%(ext)s",
        cookie_file=None,
    )
    assert cmd[0] == "yt-dlp"
    assert cmd[-1] == "https://www.douyin.com/video/1"


def test_cmd_includes_cookie_file_when_provided():
    cmd = DownloadEngine._build_ytdlp_cmd(
        url="https://www.douyin.com/video/1",
        output_template="/tmp/out.%(ext)s",
        cookie_file="/tmp/cookies.txt",
    )
    idx = cmd.index("--cookies")
    assert cmd[idx + 1] == "/tmp/cookies.txt"


def test_cmd_omits_cookies_when_no_cookie_file():
    cmd = DownloadEngine._build_ytdlp_cmd(
        url="https://www.douyin.com/video/1",
        output_template="/tmp/out.%(ext)s",
        cookie_file=None,
    )
    assert "--cookies" not in cmd


@pytest.mark.parametrize("cookie_file", [None, "/tmp/cookies.txt"])
def test_cmd_never_uses_cookies_from_browser(cookie_file):
    """回归测试：任何情况下都不能注入 ``--cookies-from-browser``。

    新版 Chrome 的 DPAPI / App-Bound Encryption 会让 yt-dlp 解 cookie 失败
    (yt-dlp#10927)，并以退出码 1 中止整个下载，因此该参数必须永不出现。
    """
    cmd = DownloadEngine._build_ytdlp_cmd(
        url="https://www.douyin.com/video/1",
        output_template="/tmp/out.%(ext)s",
        cookie_file=cookie_file,
    )
    assert "--cookies-from-browser" not in cmd
