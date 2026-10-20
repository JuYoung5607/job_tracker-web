import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    session_days: int
    cookie_secure: bool


def load_settings() -> Settings:
    return Settings(
        database_path=Path(os.getenv("DATABASE_PATH", "data/jobs.sqlite3")),
        session_days=int(os.getenv("SESSION_DAYS", "14")),
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    )


settings = load_settings()
