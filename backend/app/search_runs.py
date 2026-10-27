"""Bounded, user-triggered collection runs with persistent progress."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from nodes.job_store import SQLiteJobStore, canonical_url
from schemas.search import SearchBudget

from .config import settings
from .database import connect
from .personalization import load_search_conditions, refresh_user_feed

KST = timezone(timedelta(hours=9), name="Asia/Seoul")
DAILY_LIMIT = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_bounds(now: datetime) -> tuple[str, str, str]:
    local = now.astimezone(KST)
    start = datetime(local.year, local.month, local.day, tzinfo=KST)
    next_start = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).isoformat(),
            next_start.astimezone(timezone.utc).isoformat(),
            next_start.isoformat())


def _used_today(connection: sqlite3.Connection, user_id: str, now: datetime) -> int:
    start_utc, end_utc, _ = _day_bounds(now)
    return connection.execute(
        "SELECT COUNT(*) FROM search_runs WHERE user_id=? "
        "AND started_at>=? AND started_at<? AND "
        "(status IN ('queued','running','completed') OR external_started_at IS NOT NULL)",
        (user_id, start_utc, end_utc),
    ).fetchone()[0]


def get_daily_quota(user_id: str) -> dict:
    now = _now()
    with connect() as connection:
        used = _used_today(connection, user_id, now)
    _, _, next_midnight = _day_bounds(now)
    return {
        "daily_limit": DAILY_LIMIT,
        "remaining": max(0, DAILY_LIMIT - used),
        "next_available_at": next_midnight if used else None,
        "timezone": "Asia/Seoul",
    }


def _update(run_id: str, **values: object) -> None:
    if not values:
        return
    allowed = {
        "status", "phase", "discovered_count", "processed_count", "eligible_count",
        "review_count", "excluded_count", "failed_count", "visible_count",
        "error_message", "finished_at",
        "external_started_at",
        "new_job_ids",
    }
    if not set(values) <= allowed:
        raise ValueError("Unknown search-run field")
    columns = ", ".join(f"{key}=?" for key in values)
    with connect() as connection:
        connection.execute(
            f"UPDATE search_runs SET {columns} WHERE run_id=?",
            (*values.values(), run_id),
        )


def get_run(run_id: str, user_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM search_runs WHERE run_id=? AND user_id=?", (run_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def get_latest_run(user_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM search_runs WHERE user_id=? ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def create_run(user_id: str) -> dict:
    if load_search_conditions(user_id) is None:
        raise ValueError("먼저 검색 조건을 등록해 주세요.")
    now = _now()
    run_id = str(uuid4())
    with connect() as connection:
        # Lock the check and reservation together, including simultaneous tabs/processes.
        connection.execute("BEGIN IMMEDIATE")
        # A process restart must not leave a user blocked forever.
        connection.execute(
            "UPDATE search_runs SET status='interrupted',phase='중단됨',finished_at=? "
            "WHERE user_id=? AND status IN ('queued','running') AND started_at<?",
            (now.isoformat(), user_id, (now - timedelta(hours=1)).isoformat()),
        )
        last = connection.execute(
            "SELECT status FROM search_runs WHERE user_id=? "
            "ORDER BY started_at DESC LIMIT 1", (user_id,),
        ).fetchone()
        if last and last["status"] in {"queued", "running"}:
            raise RuntimeError("이미 공고 검색이 진행 중입니다.")
        if _used_today(connection, user_id, now) >= DAILY_LIMIT:
            raise RuntimeError("오늘의 공고 검색을 사용했습니다. 한국시간 내일 00:00부터 다시 검색할 수 있습니다.")
        try:
            connection.execute(
                "INSERT INTO search_runs(run_id,user_id,status,phase,started_at) "
                "VALUES(?,?,'queued','대기 중',?)",
                (run_id, user_id, now.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("이미 공고 검색이 진행 중입니다.") from exc
    return get_run(run_id, user_id) or {}


def run_search(run_id: str, user_id: str) -> None:
    """Runs after the HTTP response; no work waits for user feedback."""
    try:
        conditions = load_search_conditions(user_id)
        if conditions is None:
            raise RuntimeError("검색 조건을 찾을 수 없습니다.")
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

        # Import the graph only inside the worker so API startup needs no LLM key.
        from graph import build_workflow

        store = SQLiteJobStore(settings.database_path)
        external_started = False

        def mark_external_start() -> None:
            nonlocal external_started
            if not external_started:
                _update(run_id, external_started_at=_now().isoformat())
                external_started = True

        workflow = build_workflow(
            store=store, user_scoped_screening=True,
            on_external_start=mark_external_start,
        )
        budget = SearchBudget(max_search_calls=8, max_candidates=12, timeout_seconds=300)
        _update(run_id, status="running", phase="목록에서 후보 찾는 중")
        counts = {"discovered_count": 0, "processed_count": 0,
                  "eligible_count": 0, "review_count": 0,
                  "excluded_count": 0, "failed_count": 0}
        seen_candidates: set[str] = set()
        with connect() as connection:
            previous_feed = connection.execute(
                "SELECT payload FROM user_feeds WHERE user_id=?", (user_id,)
            ).fetchone()
        previous_ids = ({item["job_id"] for item in json.loads(previous_feed["payload"]).get("items", [])}
                        if previous_feed else set())

        for event in workflow.stream({
            "run_id": run_id, "user_id": user_id, "started_at": _now(),
            "search_conditions": conditions, "search_budget": budget,
        }, config={"recursion_limit": budget.max_candidates * 8 + 20}, stream_mode="updates"):
            for node_name, update in event.items():
                for candidate in update.get("candidates") or []:
                    seen_candidates.add(canonical_url(str(candidate.url)))
                for candidate in update.get("discovered_candidates") or []:
                    seen_candidates.add(canonical_url(str(candidate.url)))
                counts["discovered_count"] = len(seen_candidates)
                if node_name == "candidate_iterator_node" and update.get("current_candidate"):
                    counts["processed_count"] += 1
                result = update.get("screening_result")
                if result is not None:
                    with connect() as connection:
                        connection.execute(
                            "INSERT INTO user_screening_results(user_id,canonical_url,payload,verified_at) "
                            "VALUES(?,?,?,?) ON CONFLICT(user_id,canonical_url) DO UPDATE SET "
                            "payload=excluded.payload,verified_at=excluded.verified_at",
                            (user_id, canonical_url(str(result.source_url)),
                             result.model_dump_json(), _now().isoformat()),
                        )
                    counts[f"{result.decision.value}_count"] += 1
                counts["failed_count"] += len(update.get("errors") or [])
                phase = "후보 확인 중" if counts["processed_count"] else "목록에서 후보 찾는 중"
                _update(run_id, phase=phase, **counts)

        visible_count = refresh_user_feed(user_id)
        with connect() as connection:
            current_feed = connection.execute(
                "SELECT payload FROM user_feeds WHERE user_id=?", (user_id,)
            ).fetchone()
        current_ids = ({item["job_id"] for item in json.loads(current_feed["payload"]).get("items", [])}
                       if current_feed else set())
        new_ids = sorted(current_ids - previous_ids)
        _update(run_id, status="completed", phase=f"완료 · 새 공고 {len(new_ids)}건",
                visible_count=visible_count, new_job_ids=json.dumps(new_ids),
                finished_at=_now().isoformat(), **counts)
    except Exception as exc:
        print(f"[Search Run] {run_id} failed: {type(exc).__name__}: {exc}")
        _update(run_id, status="failed", phase="검색 실패",
                error_message=f"검색을 완료하지 못했습니다. 오류 유형: {type(exc).__name__}",
                finished_at=_now().isoformat())
