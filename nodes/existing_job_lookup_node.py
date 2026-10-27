"""Load an existing job before processing the current candidate."""

from datetime import datetime, timezone

from nodes.job_store import SQLiteJobStore, job_store
from schemas.errors import AgentError
from state import State


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


existing_job_lookup_node = make_existing_job_lookup_node(job_store)
