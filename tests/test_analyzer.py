"""AnalysisEngine 纯函数单测（不跑真实管线/DB/GPU/LLM）。"""
from backend.engine.analyzer import stage_progress


def test_stage_progress_start_of_transcribing():
    assert stage_progress("transcribing", 0.0) == 10.0


def test_stage_progress_end_of_transcribing():
    assert stage_progress("transcribing", 1.0) == 60.0


def test_stage_progress_analyzing_range():
    assert stage_progress("analyzing", 0.0) == 60.0
    assert stage_progress("analyzing", 1.0) == 99.0


def test_stage_progress_extracting():
    assert stage_progress("extracting", 0.0) == 0.0
    assert stage_progress("extracting", 1.0) == 10.0
