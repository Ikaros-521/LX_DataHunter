"""Platform credential management."""
import logging
from fastapi import APIRouter, Depends, HTTPException
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
    # 同平台全部取消
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


async def get_active_cookie(platform: str, db: AsyncSession) -> str | None:
    """获取指定平台的活跃 Cookie 字符串。供爬虫适配器调用。"""
    result = await db.execute(
        select(PlatformCredential).where(
            PlatformCredential.platform == platform,
            PlatformCredential.is_active == True,
        )
    )
    cred = result.scalar_one_or_none()
    if cred and cred.cookie_str:
        # 解析 cookie 字符串为 dict
        cookies = {}
        for item in cred.cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, _, v = item.partition("=")
                cookies[k.strip()] = v.strip()
        return cred.cookie_str
    return None
