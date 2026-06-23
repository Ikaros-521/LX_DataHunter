"""Platform info endpoint."""
from fastapi import APIRouter
from backend.schemas import PlatformInfo

router = APIRouter(prefix="/api/v1/platforms", tags=["platforms"])

SUPPORTED = [
    PlatformInfo(id="douyin", name="抖音", icon="douyin", status="active"),
    PlatformInfo(id="bilibili", name="B站", icon="bilibili", status="active"),
    PlatformInfo(id="kuaishou", name="快手", icon="kuaishou", status="coming_soon"),
    PlatformInfo(id="xiaohongshu", name="小红书", icon="xiaohongshu", status="coming_soon"),
]

@router.get("")
async def list_platforms() -> list[PlatformInfo]:
    return SUPPORTED
