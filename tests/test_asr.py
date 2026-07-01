"""ASR 纯函数与 provider 选择器的单测（不碰真实模型/网络）。"""
import pytest

from backend.engine import asr
from backend.engine.asr import AsrResult, AsrSegment


def test_segments_to_transcript_concat_and_strip():
    segs = [AsrSegment(0, 1, "你好 "), AsrSegment(1, 2, "世界。 ")]
    assert asr.segments_to_transcript(segs) == "你好 世界。"


def test_format_ts_under_an_hour():
    assert asr.format_ts(0) == "0:00"
    assert asr.format_ts(65) == "1:05"
    assert asr.format_ts(3661) == "1:01:01"


def test_segments_to_dicts_roundtrip():
    segs = [AsrSegment(0.0, 1.5, "a")]
    d = asr.segments_to_dicts(segs)
    assert d == [{"start": 0.0, "end": 1.5, "text": "a"}]
    assert asr.dict_segments_to_objects(d) == segs


def test_get_asr_provider_local_by_default(monkeypatch):
    # 默认配置 asr_provider == "local"
    assert asr.get_asr_provider().name == "local"


def test_get_asr_provider_aliyun_requires_credentials(monkeypatch):
    monkeypatch.setattr(asr.settings, "asr_provider", "aliyun")
    monkeypatch.setattr(asr.settings, "aliyun_asr_access_key", "")
    with pytest.raises(RuntimeError):
        asr.get_asr_provider()
