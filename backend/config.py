"""Application configuration via Pydantic BaseSettings."""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = f"sqlite+aiosqlite:///{Path(__file__).parent.parent / 'data.db'}"
    static_dir: Path = Path(__file__).parent / "static"
    downloads_dir: Path = Path(__file__).parent.parent / "downloads"
    reports_dir: Path = Path(__file__).parent.parent / "output" / "reports"
    crawler_headless: bool = True
    chrome_executable: str | None = None
    max_concurrent_downloads: int = 3
    max_download_retries: int = 3
    log_level: str = "INFO"

    # ---- 视频分析 ----
    asr_provider: str = "local"            # local | aliyun
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "auto"
    aliyun_asr_access_key: str = ""
    aliyun_asr_secret_key: str = ""
    aliyun_asr_app_key: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout: int = 120
    max_concurrent_analyses: int = 2
    max_analysis_retries: int = 3

    model_config = {"env_prefix": "", "env_file": ".env"}


settings = Settings()
