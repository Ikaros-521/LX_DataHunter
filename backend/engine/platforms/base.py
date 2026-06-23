"""Abstract base for platform adapters and shared types."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

from backend.models import Platform, ContentType

ProgressCallback = Callable[[int, int, str], Coroutine[Any, Any, None]]

@dataclass
class CreatorInfo:
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    extra_info: dict = field(default_factory=dict)

@dataclass
class ContentItem:
    content_id: str
    content_type: ContentType = ContentType.VIDEO
    title: str = ""
    cover_url: str = ""
    create_time: datetime | None = None
    duration_sec: float = 0
    url: str = ""
    tags: list[str] = field(default_factory=list)
    music_title: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0
    plays: int = 0
    collects: int = 0
    extra_data: dict = field(default_factory=dict)

class BasePlatformAdapter(ABC):
    platform: Platform

    @abstractmethod
    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int = 50,
        headless: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
        cookie_str: Optional[str] = None,
    ) -> tuple[CreatorInfo, list[ContentItem]]:
        """Crawl a creator's content. Returns (creator_info, content_list)."""
        ...
