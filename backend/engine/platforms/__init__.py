"""Platform adapter registry."""
from .base import BasePlatformAdapter, ContentItem, CreatorInfo
from .douyin import DouyinAdapter
from .bilibili import BilibiliAdapter
from backend.models import Platform

ADAPTERS: dict[Platform, BasePlatformAdapter] = {
    Platform.DOUYIN: DouyinAdapter(),
    Platform.BILIBILI: BilibiliAdapter(),
}

def get_adapter(platform: Platform) -> BasePlatformAdapter:
    adapter = ADAPTERS.get(platform)
    if not adapter:
        raise ValueError(f"Unsupported platform: {platform}")
    return adapter
