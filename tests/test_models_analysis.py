"""VideoAnalysis 模型基础断言。"""
from backend.models import VideoAnalysis, AnalysisStatus


def test_analysis_status_values():
    assert AnalysisStatus.PENDING.value == "pending"
    assert AnalysisStatus.EXTRACTING.value == "extracting"
    assert AnalysisStatus.TRANSCRIBING.value == "transcribing"
    assert AnalysisStatus.ANALYZING.value == "analyzing"
    assert AnalysisStatus.COMPLETED.value == "completed"
    assert AnalysisStatus.FAILED.value == "failed"


def test_video_analysis_table_columns():
    cols = {c.name for c in VideoAnalysis.__table__.columns}
    for required in {
        "id", "download_task_id", "content_snapshot_id", "platform",
        "content_id", "title", "status", "stage", "progress",
        "asr_provider", "asr_segments", "transcript", "duration_sec",
        "language", "script_analysis", "content_summary", "llm_model",
        "error_message", "retry_count", "created_at", "started_at",
        "finished_at",
    }:
        assert required in cols, f"missing column {required}"


def test_download_task_id_is_unique():
    col = VideoAnalysis.__table__.columns["download_task_id"]
    assert col.unique is True
