import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE TABLE IF NOT EXISTS job_feedback (
    feedback_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('interested', 'hold', 'not_interested')),
    reasons_json TEXT NOT NULL DEFAULT '[]',
    comment TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, job_id)
);
CREATE TABLE IF NOT EXISTS correction_requests (
    request_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    job_id TEXT,
    source_url TEXT NOT NULL,
    request_type TEXT NOT NULL CHECK(request_type IN ('correction', 'removal')),
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_stats (
    source_name TEXT PRIMARY KEY,
    request_count INTEGER NOT NULL DEFAULT 0,
    accumulated_job_count INTEGER NOT NULL DEFAULT 0,
    last_collected_at TEXT
);
CREATE TABLE IF NOT EXISTS user_search_conditions (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preference_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_screening_results (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    canonical_url TEXT NOT NULL,
    payload TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY(user_id, canonical_url)
);
CREATE TABLE IF NOT EXISTS search_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','interrupted')),
    phase TEXT NOT NULL,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    excluded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    visible_count INTEGER,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    external_started_at TEXT,
    new_job_ids TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_runs_active_user
ON search_runs(user_id) WHERE status IN ('queued','running');
"""


def initialize_database(path: Path | None = None) -> None:
    db_path = path or settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(search_runs)")}
        if "external_started_at" not in columns:
            connection.execute("ALTER TABLE search_runs ADD COLUMN external_started_at TEXT")
        if "new_job_ids" not in columns:
            connection.execute("ALTER TABLE search_runs ADD COLUMN new_job_ids TEXT")


@contextmanager
def connect():
    connection = sqlite3.connect(settings.database_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None
