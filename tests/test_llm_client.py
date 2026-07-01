"""LLM prompt 构造与 client（mock httpx）的单测。"""
import asyncio
from unittest.mock import AsyncMock, patch

from backend.engine import llm_client
from backend.engine.llm_client import LlmClient, build_script_prompt, build_summary_prompt


TRANSCRIPT = "大家好，今天介绍一款产品。"


def test_build_script_prompt_contains_transcript_and_sections():
    sys_p, user_p = build_script_prompt(TRANSCRIPT)
    assert "短视频" in sys_p
    assert TRANSCRIPT in user_p
    assert "脚本" in user_p and "拍摄" in user_p and "制作" in user_p


def test_build_summary_prompt_contains_transcript():
    sys_p, user_p = build_summary_prompt(TRANSCRIPT)
    assert TRANSCRIPT in user_p
    assert "总结" in user_p


def test_llm_client_chat_returns_content():
    client = LlmClient(base_url="http://x", api_key="k", model="m", timeout=10)
    fake_resp = {"choices": [{"message": {"content": "分析结果"}}]}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake_resp

    am = AsyncMock()
    am.post = AsyncMock(return_value=_Resp())
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=am)
    with patch.object(llm_client.httpx, "AsyncClient", return_value=cm):
        result = asyncio.new_event_loop().run_until_complete(client.chat("s", "u"))
    assert result == "分析结果"


def test_llm_client_chat_raises_when_unconfigured():
    client = LlmClient(base_url="", api_key="", model="m", timeout=10)
    try:
        asyncio.new_event_loop().run_until_complete(client.chat("s", "u"))
        assert False, "should have raised"
    except RuntimeError:
        pass
