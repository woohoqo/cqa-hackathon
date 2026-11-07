import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: str
    cors_origins: list[str]
    ai_concurrency: int
    ai_timeout_seconds: float
    dev_api_url: str

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        return cls(
            database_path=os.getenv("DATABASE_PATH", "data/cqa.sqlite3"),
            cors_origins=[s.strip() for s in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if s.strip()],
            ai_concurrency=max(1, min(10, int(os.getenv("AI_CONCURRENCY", "2")))),
            ai_timeout_seconds=max(1, float(os.getenv("AI_TIMEOUT_SECONDS", "120"))),
            dev_api_url=os.getenv("DEV_API_URL", ""),
        )
