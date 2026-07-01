"""分析 API 接入烟雾测试：用 httpx ASGITransport 直接打 ASGI app，
验证 /analysis/check 路由已挂载且返回正确结构。

不用 starlette TestClient：它与当前 httpx 0.28（移除了 app= 入参）不兼容。
ASGITransport 不触发 lifespan，但 /check 不依赖 DB/引擎，可直接测。
"""
import asyncio

import httpx

import backend.main as m


def test_analysis_check_endpoint():
    async def call():
        transport = httpx.ASGITransport(app=m.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/v1/analysis/check")

    r = asyncio.run(call())
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {
        "ffmpeg_available", "ffmpeg_path", "asr_provider",
        "asr_ready", "asr_message", "llm_ready", "llm_message",
    }
    assert data["asr_provider"] in ("local", "aliyun")
