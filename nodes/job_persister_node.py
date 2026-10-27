"""Persist processed job postings after lifecycle verification."""

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from nodes.job_store import SQLiteJobStore, canonical_url, job_store
from nodes.lifecycle_node import content_hash
from schemas.candidate import JobPosting, JobPostingDraft
from schemas.errors import AgentError
from schemas.lifecycle import LifecycleStatus
from state import State


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


job_persister_node = make_job_persister_node(job_store)
