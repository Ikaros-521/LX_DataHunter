"""视频下载引擎 — 基于 yt-dlp 的异步下载队列管理器。"""
import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from sqlalchemy import select, text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models import (
    DownloadTask, DownloadStatus, ContentSnapshot, PlatformCredential,
)

logger = logging.getLogger("fapai.downloader")

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]

# 进度广播回调类型
ProgressBroadcast = Callable[[dict], Coroutine[Any, Any, None]]


class DownloadEngine:
    """基于 asyncio 的视频下载队列管理器。

    使用 yt-dlp 作为统一下载后端，支持 Bilibili / Douyin 等平台。
    """

    def __init__(self):
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._semaphore: asyncio.Semaphore | None = None
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._broadcast: ProgressBroadcast | None = None
        self._ytdlp_available: bool | None = None
        self._ytdlp_path: str = ""
        self._ytdlp_version: str = ""

    # ---- yt-dlp 检测 ----

    async def check_ytdlp(self) -> dict:
        """检测 yt-dlp 是否可用，返回 {available, version, path}。"""
        if self._ytdlp_available is not None:
            return {
                "available": self._ytdlp_available,
                "version": self._ytdlp_version,
                "path": self._ytdlp_path,
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                version = stdout.decode().strip()
                self._ytdlp_available = True
                self._ytdlp_version = version
                # 获取 yt-dlp 路径
                which_cmd = "where" if os.name == "nt" else "which"
                proc2 = await asyncio.create_subprocess_exec(
                    which_cmd, "yt-dlp",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await proc2.communicate()
                self._ytdlp_path = stdout2.decode().strip().split("\n")[0].strip()
                logger.info(f"yt-dlp detected: version={version}, path={self._ytdlp_path}")
                return {"available": True, "version": version, "path": self._ytdlp_path}
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"yt-dlp check error: {e}")
        self._ytdlp_available = False
        return {"available": False, "version": "", "path": ""}

    @property
    def is_available(self) -> bool:
        return self._ytdlp_available is True

    # ---- 引擎生命周期 ----

    def set_broadcast(self, fn: ProgressBroadcast):
        self._broadcast = fn

    async def start(self):
        """启动下载引擎（在 FastAPI lifespan 中调用）。"""
        downloads_dir = settings.downloads_dir
        downloads_dir.mkdir(parents=True, exist_ok=True)

        ytdlp_info = await self.check_ytdlp()
        if not ytdlp_info["available"]:
            logger.warning(
                "yt-dlp not found! Download engine will NOT start. "
                "Install yt-dlp first: pip install yt-dlp"
            )
            return

        # 恢复中断的任务
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(DownloadTask)
                .where(DownloadTask.status == DownloadStatus.DOWNLOADING.value)
                .values(
                    status=DownloadStatus.PENDING.value,
                    error_message="Server restarted — download interrupted",
                )
            )
            await db.commit()
            logger.info("Reset interrupted download tasks to pending")

        # 重新入队 pending 任务
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DownloadTask.id).where(
                    DownloadTask.status == DownloadStatus.PENDING.value
                )
            )
            pending_ids = result.scalars().all()
            for tid in pending_ids:
                self._queue.put_nowait(tid)
            if pending_ids:
                logger.info(f"Re-queued {len(pending_ids)} pending tasks")

        self._semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
        self._running = True
        worker_count = settings.max_concurrent_downloads
        for i in range(worker_count):
            task = asyncio.create_task(self._worker(i))
            self._workers.append(task)
        logger.info(
            f"Download engine started: {worker_count} workers, "
            f"max_concurrent={settings.max_concurrent_downloads}"
        )

    async def stop(self):
        """停止下载引擎。"""
        self._running = False
        # 唤醒所有等待中的 worker 让它们退出
        for _ in self._workers:
            try:
                self._queue.put_nowait(-1)
            except asyncio.QueueFull:
                pass
        for w in self._workers:
            try:
                await asyncio.wait_for(w, timeout=5)
            except (asyncio.TimeoutError, Exception):
                w.cancel()
        self._workers.clear()
        logger.info("Download engine stopped")

    async def enqueue(self, task_id: int):
        """将任务加入下载队列。"""
        await self._queue.put(task_id)

    # ---- Worker ----

    async def _worker(self, worker_id: int):
        logger.info(f"Download worker-{worker_id} started")
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=2)
            except asyncio.TimeoutError:
                continue
            if task_id == -1:
                self._queue.task_done()
                break
            try:
                async with self._semaphore:
                    await self._process_task(task_id)
            except Exception as e:
                logger.error(f"Worker-{worker_id} unhandled error for task {task_id}: {e}")
            finally:
                self._queue.task_done()
        logger.info(f"Download worker-{worker_id} stopped")

    # ---- 任务处理 ----

    async def _process_task(self, task_id: int):
        """处理单个下载任务。"""
        async with AsyncSessionLocal() as db:
            task = await db.get(DownloadTask, task_id)
            if not task:
                logger.warning(f"Download task {task_id} not found")
                return

            if task.status not in (
                DownloadStatus.PENDING.value,
                DownloadStatus.FAILED.value,
            ):
                return

            # 获取内容快照
            snapshot = await db.get(ContentSnapshot, task.content_snapshot_id)
            if not snapshot:
                await self._fail_task(db, task, "关联的内容快照不存在")
                return

            # 获取 cookie
            cookie_str = await self._get_cookie(db, task.platform)

            # 更新为下载中
            task.status = DownloadStatus.DOWNLOADING.value
            task.started_at = datetime.now()
            task.progress = 0
            task.error_message = ""
            await db.commit()
            await self._broadcast_progress(task)

            # 构造下载 URL
            url = snapshot.url
            if not url:
                await self._fail_task(db, task, "内容没有有效的 URL")
                return

            # 构造输出路径
            output_dir = self._build_output_dir(task.platform)
            safe_title = self._sanitize_filename(snapshot.title or task.content_id)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_template = str(
                output_dir / f"{safe_title}_{date_str}.%(ext)s"
            )

            # 下载：按平台分流。抖音走 Playwright 拦截直链（见 _run_douyin_download），
            # 不再依赖 yt-dlp（其抖音提取器长期不稳）；其它平台继续用 yt-dlp，
            # 必要时写临时 cookie 文件。cookie_file 仅 yt-dlp 路径会用到。
            cookie_file: str | None = None
            try:
                if task.platform == "douyin":
                    file_path, file_size = await self._run_douyin_download(
                        url=url,
                        output_template=output_template,
                        task_id=task_id,
                    )
                else:
                    if cookie_str:
                        cookie_file = await self._write_cookie_file(cookie_str, url)
                    file_path, file_size = await self._run_ytdlp(
                        url=url,
                        output_template=output_template,
                        cookie_file=cookie_file,
                        task_id=task_id,
                    )

                # 更新成功
                task.status = DownloadStatus.COMPLETED.value
                task.progress = 100
                task.file_path = str(file_path)
                task.file_size = file_size
                task.finished_at = datetime.now()
                await db.commit()
                await self._broadcast_progress(task)
                logger.info(
                    f"Download completed: task={task_id}, "
                    f"file={file_path.name}, size={file_size}"
                )

            except asyncio.CancelledError:
                logger.info(f"Download task {task_id} cancelled")
                raise
            except Exception as e:
                error_msg = str(e)[:500]
                logger.error(f"Download failed: task={task_id}, error={error_msg}")
                task.retry_count += 1
                if task.retry_count >= settings.max_download_retries:
                    await self._fail_task(db, task, error_msg)
                else:
                    # 重试：重置为 pending 重新入队
                    task.status = DownloadStatus.PENDING.value
                    task.error_message = f"尝试 {task.retry_count}/{settings.max_download_retries} 失败: {error_msg}"
                    await db.commit()
                    await self._broadcast_progress(task)
                    self._queue.put_nowait(task.id)
                    logger.info(
                        f"Retry queued: task={task_id}, "
                        f"attempt {task.retry_count}/{settings.max_download_retries}"
                    )
            finally:
                if cookie_file:
                    try:
                        os.unlink(cookie_file)
                    except Exception:
                        pass

    @staticmethod
    def _build_ytdlp_cmd(
        url: str,
        output_template: str,
        cookie_file: str | None,
    ) -> list[str]:
        """构造 yt-dlp 命令行参数（纯函数，无 I/O，便于单元测试）。

        抖音已不走 yt-dlp（改用 ``_run_douyin_download`` 拦截直链），此处仅
        服务于 B 站等平台。刻意不使用 ``--cookies-from-browser``：新版 Chrome
        (127+) 用 App-Bound Encryption + DPAPI 加密 cookie，yt-dlp 在 Windows
        上解不开，会抛 "Failed to decrypt with DPAPI" (yt-dlp#10927) 并以退出码
        1 中止，连带后续 cookie_file 也用不上。需要 cookie 时统一通过 cookie_file
        传入（``PlatformCredential`` 存的 cookie）。
        """
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-update",
            "--newline",
            "--progress",
            "--print", "after_move:filepath",
            # 格式选择：优先 mp4
            "--format-sort", "vcodec:h264,ext:mp4",
            "--merge-output-format", "mp4",
            "--retries", "3",
            "--fragment-retries", "3",
            "--concurrent-fragments", "4",
            "--no-part",  # 避免生成 .part 文件
            "--encoding", "UTF-8",
            "-o", output_template,
        ]
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
        cmd.append(url)
        return cmd

    async def _run_ytdlp(
        self,
        url: str,
        output_template: str,
        cookie_file: str | None,
        task_id: int,
    ) -> tuple[Path, int]:
        """执行 yt-dlp 下载，返回 (file_path, file_size)。"""
        cmd = self._build_ytdlp_cmd(
            url=url,
            output_template=output_template,
            cookie_file=cookie_file,
        )

        logger.info(f"yt-dlp starting: task={task_id}, url={url}")
        logger.debug(f"yt-dlp cmd: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        downloaded_path: Path | None = None
        progress_match = re.compile(
            r"\b(\d+(?:\.\d+)?)%\b.*?(?:of\s+~?(\d+(?:\.\d+)?)\s*\w+)?"
        )

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()

            # 解析进度
            # yt-dlp --progress 的输出格式:
            # [download]  45.2% of ~123.45MiB at 5.67MiB/s ETA 00:15
            m = progress_match.search(line_str)
            if m:
                pct = float(m.group(1))
                # 更新进度到数据库
                await self._update_progress(task_id, pct)

        await proc.wait()

        if proc.returncode != 0:
            stderr = ""
            if proc.stderr:
                stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"yt-dlp exited with code {proc.returncode}: {stderr}"
            )

        # 获取下载路径：yt-dlp 的 --print after_move:filepath 会输出最终路径
        # 但由于我们用 --newline 逐行读，最后一行 stdout 可能是最终路径
        # 需要从 stdout 缓冲区读取
        # 由于 stdout 已读完，使用 proc.stdout.read() 获取剩余
        remaining = b""
        if proc.stdout:
            remaining = await proc.stdout.read()

        # 重新从输出目录寻找文件
        output_dir = Path(output_template).parent
        # 寻找最近修改的文件
        downloaded_path = await self._find_downloaded_file(output_dir, url)

        if not downloaded_path or not downloaded_path.exists():
            raise RuntimeError("下载完成但找不到输出文件")

        file_size = downloaded_path.stat().st_size
        return downloaded_path, file_size

    # ---- 抖音专用下载（Playwright 拦截直链） ----

    async def _run_douyin_download(
        self,
        url: str,
        output_template: str,
        task_id: int,
    ) -> tuple[Path, int]:
        """抖音专用下载：Playwright 拦截 aweme detail 接口拿无水印直链，httpx 下载。

        yt-dlp 的抖音提取器长期不稳定（频繁报 "Fresh cookies needed"、空 JSON），
        这里改让浏览器自身完成签名/反爬，直接拦截 ``/aweme/v1/web/aweme/detail``
        的响应拿到视频直链，再用 httpx 流式下载。复用了 hot_crawler 已验证的
        detail 拦截套路。返回 (file_path, file_size)。
        """
        import httpx
        from playwright.async_api import async_playwright

        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        detail_pattern = "/aweme/v1/web/aweme/detail"
        captured: dict = {}

        chrome_exe = next((p for p in CHROME_PATHS if os.path.exists(p)), None)

        logger.info(f"[douyin] intercepting video url for task={task_id}, url={url}")
        async with async_playwright() as p:
            launch_args: dict[str, Any] = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            }
            if chrome_exe:
                launch_args["executable_path"] = chrome_exe
            browser = await p.chromium.launch(**launch_args)
            try:
                context = await browser.new_context(user_agent=ua, locale="zh-CN")
                page = await context.new_page()

                async def on_resp(response):
                    try:
                        if detail_pattern in response.url:
                            captured["detail"] = await response.json()
                    except Exception:
                        pass

                page.on("response", on_resp)
                # 直接导航目标 url（含短链），浏览器自动跟跳转；detail XHR 由页面 JS 发起
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                # 轮询等待 detail XHR 返回（最多 ~15s）
                for _ in range(30):
                    if "detail" in captured:
                        break
                    await asyncio.sleep(0.5)

                cookies = await context.cookies()
            finally:
                await browser.close()

        detail = captured.get("detail") or {}
        aweme = detail.get("aweme_detail") or {}
        video_url = self._pick_douyin_video_url(aweme)
        if not video_url:
            raise RuntimeError(
                "未能从抖音详情接口拦截到视频直链"
                "（可能弹了验证码或页面结构变化）"
                f"；aweme_detail keys={list(aweme.keys())[:8]}"
            )
        video_url = self._to_no_watermark(video_url)

        # 用浏览器拿到的 cookie + UA/Referer 直接下载，规避反爬
        cookie_header = "; ".join(
            f"{c['name']}={c['value']}"
            for c in cookies
            if "douyin" in (c.get("domain") or "")
        )
        headers = {
            "User-Agent": ua,
            "Referer": "https://www.douyin.com/",
            "Cookie": cookie_header,
        }

        output_path = Path(output_template.replace("%(ext)s", "mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[douyin] downloading task={task_id} -> {video_url[:120]}...")
        written = 0
        total = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            async with client.stream("GET", video_url) as r:
                if r.status_code != 200:
                    body = (await r.aread())[:300]
                    raise RuntimeError(
                        f"视频下载 HTTP {r.status_code}: {body!r}"
                    )
                total = int(r.headers.get("Content-Length", "0") or 0)
                with open(output_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
                        written += len(chunk)
                        if total:
                            await self._update_progress(
                                task_id, written / total * 100
                            )

        if total:
            await self._update_progress(task_id, 100)
        return output_path, output_path.stat().st_size

    @staticmethod
    def _pick_douyin_video_url(aweme_detail: dict) -> str | None:
        """从 aweme_detail 里取视频直链。

        优先 ``video.play_addr.url_list``，回退 ``play_addr_h264``，再回退
        ``bit_rate[].play_addr``。纯函数，便于单测。
        """
        video = (aweme_detail or {}).get("video") or {}
        for key in ("play_addr", "play_addr_h264"):
            url_list = (video.get(key) or {}).get("url_list") or []
            if url_list and url_list[0]:
                return url_list[0]
        for br in video.get("bit_rate") or []:
            url_list = ((br or {}).get("play_addr") or {}).get("url_list") or []
            if url_list and url_list[0]:
                return url_list[0]
        return None

    @staticmethod
    def _to_no_watermark(url: str) -> str:
        """把抖音 play_addr 直链转为无水印版本。

        抖音视频 URL 路径里的 ``/playwm/`` 表示带水印，换成 ``/play/`` 即无水印
        （社区通用、长期稳定的做法）。若 URL 不含该标记则原样返回——仍可下载，
        只是可能带水印。
        """
        if "/playwm/" in url:
            return url.replace("/playwm/", "/play/", 1)
        return url

    async def _find_downloaded_file(self, output_dir: Path, url: str) -> Path | None:
        """在输出目录中查找最近下载的文件。"""
        if not output_dir.exists():
            return None
        # 找最新的 mp4 / webm / mkv 文件
        candidates = []
        for ext in ("*.mp4", "*.webm", "*.mkv", "*.flv", "*.avi"):
            candidates.extend(output_dir.glob(ext))
        if not candidates:
            return None
        # 按修改时间倒序，取最新的
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        # 取最近 30 秒内修改的文件
        import time as _time
        now = _time.time()
        for c in candidates:
            if now - c.stat().st_mtime < 60:
                return c
        return candidates[0]

    # ---- 辅助方法 ----

    async def _update_progress(self, task_id: int, progress: float):
        """更新任务进度并广播。"""
        async with AsyncSessionLocal() as db:
            task = await db.get(DownloadTask, task_id)
            if task and task.status == DownloadStatus.DOWNLOADING.value:
                task.progress = min(progress, 99.9)
                await db.commit()
                await self._broadcast_progress(task)

    async def _fail_task(self, db: AsyncSession, task: DownloadTask, error_msg: str):
        """将任务标记为失败。"""
        from backend.models import beijing_now
        task.status = DownloadStatus.FAILED.value
        task.error_message = error_msg
        task.finished_at = beijing_now()
        await db.commit()
        await self._broadcast_progress(task)

    async def _get_cookie(self, db: AsyncSession, platform: str) -> str | None:
        """获取平台的活跃 cookie。"""
        result = await db.execute(
            select(PlatformCredential).where(
                PlatformCredential.platform == platform,
                PlatformCredential.is_active == True,
            )
        )
        cred = result.scalar_one_or_none()
        return cred.cookie_str if cred else None

    async def _write_cookie_file(self, cookie_str: str, url: str = "") -> str | None:
        """将 cookie 字符串写入 Netscape 格式的临时文件。

        从 URL 推断 domain，使 cookie 能正确匹配目标网站。
        """
        try:
            # 从 URL 提取域名
            domain = ""
            if url:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                hostname = parsed.hostname or ""
                # Netscape cookie 要求以点开头（表示包含子域名）
                if hostname:
                    parts = hostname.split(".")
                    if len(parts) >= 2:
                        domain = "." + ".".join(parts[-2:])
                    else:
                        domain = hostname

            lines = [
                "# Netscape HTTP Cookie File",
                "# This is a generated file!  Do not edit.",
                "",
            ]
            # Netscape 格式: domain, flag, path, secure, expires, name, value
            # expires: 大约 10 年后的 Unix 时间戳，保证长期有效
            long_expiry = int(datetime.now().timestamp()) + 10 * 365 * 86400
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    name, _, value = item.partition("=")
                    lines.append(
                        f"{domain}\tTRUE\t/\tTRUE\t{long_expiry}\t{name.strip()}\t{value.strip()}"
                    )
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="ytdlp_cookies_")
            os.write(fd, "\n".join(lines).encode("utf-8"))
            os.close(fd)
            logger.debug(f"Wrote cookie file: {path}, domain={domain}, cookies={len(lines) - 3}")
            return path
        except Exception as e:
            logger.warning(f"Failed to write cookie file: {e}")
            return None

    def _build_output_dir(self, platform: str) -> Path:
        """构造下载输出目录: downloads/{platform}/"""
        base = settings.downloads_dir / platform
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _sanitize_filename(name: str, max_len: int = 100) -> str:
        """清理文件名中的非法字符。"""
        # 移除 Windows 非法字符
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        # 去除首尾空格和点号
        name = name.strip(' .')
        # 截断
        if len(name) > max_len:
            name = name[:max_len]
        return name or "untitled"

    async def _broadcast_progress(self, task: DownloadTask):
        """通过 WebSocket 广播任务进度。"""
        if self._broadcast:
            try:
                await self._broadcast({
                    "task_id": task.id,
                    "content_snapshot_id": task.content_snapshot_id,
                    "platform": task.platform,
                    "content_id": task.content_id,
                    "title": task.title,
                    "status": task.status,
                    "progress": task.progress,
                    "file_path": task.file_path,
                    "file_size": task.file_size,
                    "error_message": task.error_message,
                })
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")


# 全局单例
download_engine = DownloadEngine()
