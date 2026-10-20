"""Persist common job postings across runs, without user-specific screening data."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from nodes.lifecycle_node import content_hash
from schemas.candidate import JobPosting, JobPostingDraft
from schemas.errors import AgentError
from schemas.lifecycle import LifecycleStatus
from schemas.screening import ScreeningResult
from state import State


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.sqlite3"


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


def make_existing_job_lookup_node(store: SQLiteJobStore):
    def existing_job_lookup_node(state: State) -> dict:
        candidate = state.get("current_candidate")
        if candidate is None:
            return {"existing_job": None}
        try:
            existing = store.get(str(candidate.url))
            print(f"[Job Lookup] {candidate.url}: {'기존 공고 있음' if existing else '신규 후보'}")
            return {"existing_job": existing}
        except Exception as exc:
            print(f"[Job Lookup] 조회 실패: {type(exc).__name__}: {exc}")
            return {"existing_job": None, "errors": [AgentError(
                node_name="job_lookup", error_type=type(exc).__name__,
                message=f"기존 공고 조회 실패: {candidate.url}",
                occurred_at=datetime.now(timezone.utc), retryable=True,
            )]}
    return existing_job_lookup_node


def make_job_persister_node(store: SQLiteJobStore):
    def job_persister_node(state: State) -> dict:
        result = state.get("lifecycle_result")
        draft = state.get("job_posting_draft")
        document = state.get("job_document")
        existing = state.get("existing_job")
        screening = state.get("screening_result")
        if screening is not None:
            try:
                store.save_screening(screening)
            except Exception as exc:
                print(f"[Job Persister] 검증 결과 저장 실패: {type(exc).__name__}: {exc}")
        if result is None:
            return {"persisted_job_id": None}
        if result.next_status in {LifecycleStatus.UNKNOWN, LifecycleStatus.UNAVAILABLE}:
            print(f"[Job Persister] {result.next_status.value}: 기존 기록 보존, 저장 생략")
            return {"persisted_job_id": existing.job_id if existing else None}
        if draft is None or document is None:
            print("[Job Persister] 공고 초안 또는 원문 없음: 저장 생략")
            return {"persisted_job_id": existing.job_id if existing else None}

        now = datetime.now(timezone.utc)
        new_hash = content_hash(document.content)
        evidence = {item.evidence_id: item for item in existing.evidence} if existing else {}
        evidence.update({item.evidence_id: item for item in state.get("evidence") or []})
        new_status = result.next_status
        changed = bool(existing and (
            result.changed_fields or new_status == LifecycleStatus.CLOSED
            and existing.lifecycle_status != LifecycleStatus.CLOSED
        ))
        # Only confirmed changed fields may overwrite existing structured data.
        # Otherwise an unstable extraction would silently replace a good value.
        posting_data = draft.model_dump() if existing is None else {
            **existing.model_dump(include=set(JobPostingDraft.model_fields)),
            **draft.model_dump(include=set(result.changed_fields)),
        }
        job = JobPosting(
            **posting_data,
            job_id=existing.job_id if existing else str(uuid5(NAMESPACE_URL, canonical_url(str(draft.source_url)))),
            lifecycle_status=new_status,
            source_urls=list(dict.fromkeys([
                *(existing.source_urls if existing else []), draft.source_url,
            ])),
            evidence=list(evidence.values()),
            content_hash=new_hash,
            first_seen_at=existing.first_seen_at if existing else now,
            last_seen_at=now,
            created_at=existing.created_at if existing else now,
            updated_at=now if changed or not existing else existing.updated_at,
            result_version=(existing.result_version + 1 if changed else
                            existing.result_version if existing else 1),
        )
        if existing:
            job.status = existing.status
            job.source_status = existing.source_status
        try:
            store.save(job)
        except Exception as exc:
            print(f"[Job Persister] 저장 실패: {type(exc).__name__}: {exc}")
            return {"persisted_job_id": None, "errors": [AgentError(
                node_name="job_persister", error_type=type(exc).__name__,
                message=f"공고 저장 실패: {draft.source_url}",
                occurred_at=now, retryable=True,
            )]}
        print(f"[Job Persister] {draft.source_url}: {new_status.value}, 버전={job.result_version}")
        return {"persisted_job_id": job.job_id}
    return job_persister_node


job_store = SQLiteJobStore(os.getenv("JOB_TRACKER_DB_PATH") or DEFAULT_DB_PATH)
existing_job_lookup_node = make_existing_job_lookup_node(job_store)
job_persister_node = make_job_persister_node(job_store)
