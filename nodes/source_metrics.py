"""수집 출처별 요청 수와 발견한 고유 공고 수를 기록합니다."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.sqlite3"


def record_source_request(source_name: str, discovered_urls: list[str]) -> None:
    path = Path(os.getenv("JOB_TRACKER_DB_PATH") or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path, timeout=10) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS source_stats ("
                "source_name TEXT PRIMARY KEY, request_count INTEGER NOT NULL DEFAULT 0, "
                "accumulated_job_count INTEGER NOT NULL DEFAULT 0, last_collected_at TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS source_discoveries ("
                "source_name TEXT NOT NULL, source_url TEXT NOT NULL, "
                "first_discovered_at TEXT NOT NULL, PRIMARY KEY(source_name, source_url))"
            )
            now = datetime.now(timezone.utc).isoformat()
            connection.executemany(
                "INSERT OR IGNORE INTO source_discoveries(source_name,source_url,first_discovered_at) "
                "VALUES(?,?,?)",
                [(source_name, url, now) for url in discovered_urls],
            )
            unique_count = connection.execute(
                "SELECT COUNT(*) FROM source_discoveries WHERE source_name=?", (source_name,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO source_stats(source_name,request_count,accumulated_job_count,last_collected_at) "
                "VALUES(?,1,?,?) ON CONFLICT(source_name) DO UPDATE SET "
                "request_count=source_stats.request_count+1, "
                "accumulated_job_count=excluded.accumulated_job_count, "
                "last_collected_at=excluded.last_collected_at",
                (source_name, unique_count, now),
            )
    except sqlite3.Error as exc:
        # 관측 실패가 실제 후보 수집을 중단시키면 안 됩니다.
        print(f"[Source Metrics] 기록 실패: {type(exc).__name__}")
