"""分析 API 接入烟雾测试：TestClient 启动 app，验证 /analysis/check 与路由挂载。"""
from fastapi.testclient import TestClient
import backend.main as m


def test_analysis_check_endpoint():
    client = TestClient(m.app)
    r = client.get("/api/v1/analysis/check")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"ffmpeg_available", "ffmpeg_path", "asr_provider", "asr_ready", "asr_message", "llm_ready", "llm_message"}
    assert data["asr_provider"] in ("local", "aliyun")
