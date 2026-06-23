"""Application configuration via Pydantic BaseSettings."""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = f"sqlite+aiosqlite:///{Path(__file__).parent.parent / 'data.db'}"
    static_dir: Path = Path(__file__).parent / "static"
    crawler_headless: bool = True
    chrome_executable: str | None = None
    log_level: str = "INFO"

    model_config = {"env_prefix": "FAPAI_", "env_file": ".env"}


settings = Settings()
