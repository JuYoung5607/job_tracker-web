"""Shared SQLite storage for job postings and screening results."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from schemas.candidate import JobPosting
from schemas.lifecycle import LifecycleStatus
from schemas.screening import ScreeningResult


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.sqlite3"
KOREA_TIME = timezone(timedelta(hours=9))


def canonical_url(url: str) -> str:
    parts = urlsplit(str(url))
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."),
                       parts.path.rstrip("/") or "/", query, ""))


class SQLiteJobStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "canonical_url TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, "
                "payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS screening_results ("
                "canonical_url TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, url: str) -> JobPosting | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE canonical_url = ?", (canonical_url(url),)
            ).fetchone()
        return JobPosting.model_validate_json(row[0]) if row else None

    def get_by_job_id(self, job_id: str) -> JobPosting | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return JobPosting.model_validate_json(row[0]) if row else None

    def save(self, job: JobPosting) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs (canonical_url, job_id, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(canonical_url) DO UPDATE SET payload=excluded.payload",
                (canonical_url(str(job.source_url)), job.job_id, job.model_dump_json()),
            )

    def close_past_deadline(self, now: datetime | None = None) -> int:
        """Keep historical records, but close postings after their Korean closing date."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must include a timezone")
        today = current.astimezone(KOREA_TIME).date()
        changed = 0
        with self._connect() as connection:
            rows = connection.execute("SELECT canonical_url,payload FROM jobs").fetchall()
            for url, payload in rows:
                job = JobPosting.model_validate_json(payload)
                if job.lifecycle_status == LifecycleStatus.CLOSED or job.closes_at is None:
                    continue
                closing_date = (job.closes_at.astimezone(KOREA_TIME).date()
                                if job.closes_at.tzinfo else job.closes_at.date())
                if closing_date >= today:
                    continue
                closed = job.model_copy(update={
                    "lifecycle_status": LifecycleStatus.CLOSED,
                    "updated_at": current,
                    "result_version": job.result_version + 1,
                })
                connection.execute("UPDATE jobs SET payload=? WHERE canonical_url=?",
                                   (closed.model_dump_json(), url))
                changed += 1
        return changed

    def save_screening(self, result: ScreeningResult) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO screening_results(canonical_url,payload) VALUES(?,?) "
                "ON CONFLICT(canonical_url) DO UPDATE SET payload=excluded.payload",
                (canonical_url(str(result.source_url)), result.model_dump_json()),
            )

    def list_screenings(self) -> list[ScreeningResult]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM screening_results").fetchall()
        return [ScreeningResult.model_validate_json(row[0]) for row in rows]


job_store = SQLiteJobStore(os.getenv("JOB_TRACKER_DB_PATH") or DEFAULT_DB_PATH)
