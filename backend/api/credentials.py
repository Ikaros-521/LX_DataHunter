"""Platform credential management."""
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc
from backend.database import get_db
from backend.models import PlatformCredential
from backend.schemas import CredentialCreate, CredentialResponse

logger = logging.getLogger("fapai.credentials")
router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    platform: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(PlatformCredential).order_by(desc(PlatformCredential.updated_at))
    if platform:
        stmt = stmt.where(PlatformCredential.platform == platform)
    rows = (await db.execute(stmt)).scalars().all()
    return [CredentialResponse(
        id=r.id, platform=r.platform, name=r.name, cookie_str=r.cookie_str,
        is_active=r.is_active, created_at=r.created_at, updated_at=r.updated_at,
    ) for r in rows]


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(body: CredentialCreate, db: AsyncSession = Depends(get_db)):
    cred = PlatformCredential(
        platform=body.platform,
        name=body.name or f"{body.platform}_cookie",
        cookie_str=body.cookie_str.strip(),
        is_active=True,
    )
    # 将同平台其他凭证设为非活跃
    await db.execute(
        update(PlatformCredential)
        .where(PlatformCredential.platform == body.platform)
        .values(is_active=False)
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    logger.info(f"Credential created: id={cred.id}, platform={cred.platform}, name={cred.name}")
    return CredentialResponse(
        id=cred.id, platform=cred.platform, name=cred.name, cookie_str=cred.cookie_str,
        is_active=cred.is_active, created_at=cred.created_at, updated_at=cred.updated_at,
    )


@router.put("/{credential_id}/activate")
async def activate_credential(credential_id: int, db: AsyncSession = Depends(get_db)):
    cred = await db.get(PlatformCredential, credential_id)
    if not cred:
        raise HTTPException(404, f"Credential not found: id={credential_id}")
    await db.execute(
        update(PlatformCredential)
        .where(PlatformCredential.platform == cred.platform)
        .values(is_active=False)
    )
    cred.is_active = True
    await db.commit()
    return {"message": f"Activated credential id={credential_id} for {cred.platform}"}


@router.delete("/{credential_id}")
async def delete_credential(credential_id: int, db: AsyncSession = Depends(get_db)):
    cred = await db.get(PlatformCredential, credential_id)
    if not cred:
        raise HTTPException(404, f"Credential not found: id={credential_id}")
    await db.delete(cred)
    await db.commit()
    return {"message": f"Deleted credential id={credential_id}"}


# ---- 浏览器自动登录 ----

LOGIN_URLS = {
    "douyin": "https://www.douyin.com/",
    "bilibili": "https://passport.bilibili.com/login",
    "kuaishou": "https://www.kuaishou.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
}

# 登录态标识 Cookie —— 这些 cookie 只有真正登录后才会出现，
# 用作 DOM 检测之后的二次校验，防止误判（例如首页 feed 里的头像导致的假阳性）。
AUTH_COOKIES = {
    "douyin": {"sessionid", "sessionid_ss", "sid_guard", "sid_tt", "uid_tt_ss"},
    "bilibili": {"SESSDATA", "bili_jct", "DedeUserID"},
    "kuaishou": {"kuaishou.server.web_st", "userId", "passToken"},
    "xiaohongshu": {"web_session", "customer-session"},
}


async def _check_logged_in_dom(platform: str, page) -> bool:
    """通过右上角 DOM 判断是否已登录。

    只检查页面右上角的「登录入口」与「用户头像」区域，避免匹配到首页
    feed 流里的作者头像（那些无论登录与否都存在，会导致误判）。
    """
    try:
        if platform == "douyin":
            # 已登录的确凿信号（基于实际 DOM 验证）：
            #   1) a[href*="/user/self"] —— 右上角用户头像/昵称的链接，
            #      登录后必现，且指向本人主页，不会误匹配 feed 流里其他作者的链接
            #   2) "退出登录" —— 登录后才有的退出按钮
            # 两者满足其一即认为已登录（不再依赖「登录入口消失」的反向判断，避免
            # 猜错登录按钮选择器导致永远不触发）。
            self_link = await page.query_selector('a[href*="/user/self"]')
            if self_link is not None:
                return True
            logout_btn = await page.query_selector('text=退出登录')
            return logout_btn is not None
        elif platform == "bilibili":
            login_btn = await page.query_selector('text=登录')
            avatar = await page.query_selector(
                '[class*="header-login-avatar"], [class*="bili-avatar"]'
            )
            return avatar is not None and login_btn is None
        else:
            # 通用：登录按钮消失即视为已登录
            login_btn = await page.query_selector('text=登录')
            return login_btn is None
    except Exception as e:
        logger.debug(f"DOM login check error: {e}")
        return False


def _has_auth_cookies(platform: str, cookies: list[dict]) -> bool:
    """检查 cookie 中是否存在登录态标识，作为 DOM 检测的二次保险。"""
    auth_names = AUTH_COOKIES.get(platform, set())
    if not auth_names:
        return True  # 未知平台不做 cookie 校验，仅依赖 DOM
    names = {c.get("name") for c in cookies}
    hit = auth_names & names
    return bool(hit)


@router.post("/auto-login")
async def auto_login(platform: str, headless: bool = False):
    """打开浏览器让用户登录，确认登录成功后再捕获 Cookie。返回 cookie_str。"""
    if platform not in LOGIN_URLS:
        raise HTTPException(400, f"Unsupported platform for auto-login: {platform}")

    from playwright.async_api import async_playwright
    import os

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)

    logger.info(f"Starting auto-login for {platform} (headless={headless})")

    cookies: list[dict] = []
    logged_in = False
    last_status = "未检测到登录"

    async with async_playwright() as p:
        launch_args = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-setuid-sandbox"],
        }
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe

        browser = await p.chromium.launch(**launch_args)
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                locale="zh-CN",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            login_url = LOGIN_URLS[platform]
            logger.info(f"Navigating to login page: {login_url}")
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

            # 轮询检测登录状态（最多 5 分钟）。双重校验：
            #   1) 右上角 DOM（登录入口消失 + 用户头像出现）
            #   2) cookie 中出现登录态标识
            # 只有两项都通过才认为登录成功，才会捕获 cookie。
            logger.info(f"Waiting for user to log in (timeout: 300s)...")
            for i in range(150):  # 150 * 2s = 300s 超时
                await asyncio.sleep(2)
                try:
                    dom_ok = await _check_logged_in_dom(platform, page)
                    if not dom_ok:
                        last_status = "未检测到登录（右上角仍有登录入口）"
                        continue
                    # DOM 已显示已登录，再用 cookie 二次确认，避免误判
                    cookies = await context.cookies()
                    if _has_auth_cookies(platform, cookies):
                        logged_in = True
                        # 等待登录态 cookie 完全写入
                        await asyncio.sleep(3)
                        logger.info(
                            f"Login confirmed for {platform} "
                            f"(DOM ✓ + auth cookies ✓: "
                            f"{sorted(AUTH_COOKIES.get(platform, set()) & {c.get('name') for c in cookies})})"
                        )
                        break
                    last_status = "DOM 显示已登录，但缺少登录态 cookie，继续等待..."
                    if i % 5 == 0:
                        logger.info(last_status)
                except Exception as e:
                    logger.debug(f"Login poll error: {e}")
                    continue

            if not logged_in:
                logger.warning(f"Login not confirmed for {platform}: {last_status}")
        finally:
            await browser.close()

    # 关键修复：未确认登录则不返回 cookie，避免捕获到游客/匿名 cookie
    if not logged_in or not cookies:
        raise HTTPException(
            408,
            f"未检测到登录成功，已放弃捕获 Cookie。请在 5 分钟内完成登录后重试。"
            f"（最后状态：{last_status}）",
        )

    # 将 Playwright cookies 转为 HTTP Cookie 字符串
    cookie_parts = [f"{c['name']}={c['value']}" for c in cookies
                    if c.get('name') and c.get('value')]
    cookie_str = "; ".join(cookie_parts)

    logger.info(f"Captured {len(cookie_parts)} cookies for {platform}: "
                f"names={[c['name'] for c in cookies[:8]]}...")

    return JSONResponse({
        "cookie_str": cookie_str,
        "cookie_count": len(cookie_parts),
        "platform": platform,
    })


async def get_active_cookie(platform: str, db: AsyncSession) -> str | None:
    """获取指定平台的活跃 Cookie 字符串。供爬虫适配器调用。"""
    result = await db.execute(
        select(PlatformCredential).where(
            PlatformCredential.platform == platform,
            PlatformCredential.is_active == True,
        )
    )
    cred = result.scalar_one_or_none()
    return cred.cookie_str if cred else None
