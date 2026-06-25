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

LOGIN_DETECT = None  # 改用 DOM 检测替代 URL 检测


@router.post("/auto-login")
async def auto_login(platform: str, headless: bool = False):
    """打开浏览器让用户登录，自动捕获 Cookie。返回 cookie_str。"""
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

    async with async_playwright() as p:
        launch_args = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-setuid-sandbox"],
        }
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        login_url = LOGIN_URLS[platform]
        logger.info(f"Navigating to login page: {login_url}")
        await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

        # 等待用户完成登录（最多 5 分钟），使用 DOM 检测登录状态
        logger.info(f"Waiting for user to log in (timeout: 300s)...")

        try:
            for _ in range(150):  # 150 * 2s = 300s 超时
                await asyncio.sleep(2)
                try:
                    if platform == "bilibili":
                        # B站：检查页面上是否有登录按钮（未登录）vs 用户头像（已登录）
                        login_btn = await page.query_selector('text=登录')
                        avatar = await page.query_selector('[class*="header-login-avatar"], [class*="bili-avatar"]')
                        logged_in = avatar is not None and login_btn is None
                    elif platform == "douyin":
                        # 抖音：检查右上角是否有登录按钮（"登录" 文字按钮）vs 用户头像
                        login_btn = await page.query_selector('text=登录')
                        # 抖音右上角登录按钮通常在一个特定容器里
                        if not login_btn:
                            login_btn = await page.query_selector('[data-e2e="login"], [class*="login-btn"], [class*="LoginButton"]')
                        # 用户头像/消息按钮（登录后出现）
                        avatar = await page.query_selector('[data-e2e="user-avatar"], [class*="avatar"], [class*="Avatar"], [class*="header-right"] img')
                        logged_in = avatar is not None and login_btn is None
                    else:
                        # 通用：检查登录按钮是否消失
                        login_btn = await page.query_selector('text=登录')
                        logged_in = login_btn is None

                    if logged_in:
                        logger.info(f"Login detected for {platform} (DOM check: login_btn gone, avatar present)")
                        await asyncio.sleep(3)
                        break
                except Exception:
                    continue
            else:
                logger.warning(f"Login timeout for {platform}")
        finally:
            # 无论超时与否，都提取当前 cookie
            cookies = await context.cookies()
            await browser.close()

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
