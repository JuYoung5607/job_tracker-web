"""Recheck imported feed URLs without running the collection Searcher.

Preview is read-only. --apply fetches each selected source page and invokes the
Extractor once per accessible posting. Results are scoped to one user because
the current Verifier uses that user's search conditions.
"""

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from nodes.job_store import SQLiteJobStore, canonical_url
from schemas.candidate import Candidate, CandidatePageType, JobPosting, SourceType
from schemas.screening import ScreeningResult
from schemas.search import SearchBudget, SearchConditions


IMPORTED_REVIEW_REASON = "이전 DB에 원문 검증 결과가 없어 모집 상태·직무·경력을 다시 확인해야 합니다."


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def pending_jobs(db_path: Path, user_id: str | None, *, include_processed: bool = False
                 ) -> tuple[str, SearchConditions, list[JobPosting]]:
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        users = connection.execute("SELECT user_id,payload FROM user_search_conditions").fetchall()
        matched = [row for row in users if user_id is None or row[0] == user_id]
        if len(matched) != 1:
            raise ValueError("대상 계정을 하나 선택할 수 없습니다. --user-id를 지정하세요.")
        selected_user, conditions_json = matched[0]
        conditions = SearchConditions.model_validate_json(conditions_json)
        feed_row = connection.execute(
            "SELECT payload FROM user_feeds WHERE user_id=?", (selected_user,)
        ).fetchone()
        if feed_row is None:
            return selected_user, conditions, []
        items = json.loads(feed_row[0]).get("items", [])
        scoped = set()
        if _has_table(connection, "user_screening_results"):
            scoped = {row[0] for row in connection.execute(
                "SELECT canonical_url FROM user_screening_results WHERE user_id=?",
                (selected_user,),
            )}
        jobs = []
        for item in items:
            url = canonical_url(item["source_url"])
            if url in scoped and not include_processed:
                continue
            screening_row = connection.execute(
                "SELECT payload FROM screening_results WHERE canonical_url=?", (url,)
            ).fetchone()
            if screening_row is None or IMPORTED_REVIEW_REASON not in ScreeningResult.model_validate_json(
                screening_row[0]
            ).reasons:
                continue
            job_row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id=?", (item["job_id"],)
            ).fetchone()
            if job_row is not None:
                jobs.append(JobPosting.model_validate_json(job_row[0]))
        return selected_user, conditions, jobs
    finally:
        connection.close()


def reverify(db_path: Path, user_id: str, conditions: SearchConditions,
             jobs: list[JobPosting]) -> Counter:
    from backend.app.database import connect, initialize_database
    from backend.app.personalization import refresh_user_feed
    from langchain_openai import ChatOpenAI
    from nodes.company_enricher_node import company_enricher_node
    from nodes.extractor_node import create_extractor_node
    from nodes.job_persister_node import make_job_persister_node
    from nodes.lifecycle_node import lifecycle_node
    from nodes.navigator_node import make_navigator_node
    from nodes.page_fetcher import make_page_fetcher
    from nodes.source_metrics import record_source_request
    from nodes.verifier_node import verifier_node

    initialize_database(db_path)
    store = SQLiteJobStore(db_path)
    fetch_page = make_page_fetcher()

    def measured_fetch(url: str):
        host = urlsplit(url).hostname or "unknown"
        source_name = {
            "www.jobkorea.co.kr": "JobKorea", "jobkorea.co.kr": "JobKorea",
            "job.incruit.com": "Incruit", "www.saramin.co.kr": "Saramin",
            "jumpit.saramin.co.kr": "Jumpit",
        }.get(host, host)
        record_source_request(source_name, [url] if "/position/" in url or
                              "/GI_Read/" in url or "rec_idx=" in url or
                              "/jobdb_info/jobpost.asp" in url else [])
        return fetch_page(url)

    navigator = make_navigator_node(measured_fetch)
    extractor = create_extractor_node(ChatOpenAI(
        model=os.getenv("OPENAI_EXTRACT_MODEL", "gpt-4.1-mini"),
        temperature=0, max_retries=0,
    ))
    persister = make_job_persister_node(store)
    counts: Counter = Counter()
    now = datetime.now(timezone.utc)

    def save_user_result(result: ScreeningResult) -> None:
        with connect() as connection:
            connection.execute(
                "INSERT INTO user_screening_results(user_id,canonical_url,payload,verified_at) "
                "VALUES(?,?,?,?) ON CONFLICT(user_id,canonical_url) DO UPDATE SET "
                "payload=excluded.payload,verified_at=excluded.verified_at",
                (user_id, canonical_url(str(result.source_url)), result.model_dump_json(),
                 datetime.now(timezone.utc).isoformat()),
            )

    for index, job in enumerate(jobs, 1):
        candidate = Candidate(
            candidate_id=str(uuid5(NAMESPACE_URL, canonical_url(str(job.source_url)))),
            url=job.source_url, page_type=CandidatePageType.JOB_DETAIL,
            source_type=SourceType.JOB_PLATFORM,
            source_name=urlsplit(str(job.source_url)).hostname or "unknown",
            discovery_source="saved_job_reverification", discovered_at=now,
        )
        state = {
            "run_id": str(uuid4()), "user_id": user_id, "started_at": now,
            "search_conditions": conditions,
            "search_budget": SearchBudget(max_search_calls=1, max_candidates=1),
            "current_candidate": candidate, "existing_job": job, "evidence": [],
        }
        print(f"[재검증 {index}/{len(jobs)}] {job.job_title}: {job.source_url}")
        try:
            state.update(navigator(state))
            if state.get("job_document") is None:
                result = state.get("screening_result")
                if result is not None:
                    save_user_result(result)
                    counts[result.decision.value] += 1
                else:
                    counts["access_failed"] += 1
                continue
            state.update(extractor(state))
            if state.get("extracted_facts") is None:
                result = state.get("screening_result")
                if result is not None:
                    save_user_result(result)
                    counts[result.decision.value] += 1
                else:
                    counts["extraction_failed"] += 1
                continue
            state.update(company_enricher_node(state))
            state.update(verifier_node(state))
            state.update(lifecycle_node(state))
            result = state["screening_result"]
            # The persister updates shared job facts/lifecycle. The user's
            # eligibility result is saved separately below.
            persister({**state, "screening_result": None})
            save_user_result(result)
            counts[result.decision.value] += 1
            print(f"[재검증] {result.decision.value}: {'; '.join(result.reasons[:2])}")
        except Exception as exc:
            counts["failed"] += 1
            print(f"[재검증] 실패 ({type(exc).__name__}): {exc}")
    counts["visible_after_refresh"] = refresh_user_feed(user_id)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/jobs.sqlite3"))
    parser.add_argument("--user-id", help="검색 조건을 가진 계정이 여러 개일 때 지정")
    parser.add_argument("--limit", type=int, help="이번 실행에서 처리할 최대 공고 수")
    parser.add_argument("--include-processed", action="store_true",
                        help="이미 사용자별 판정이 저장된 공고도 재확인")
    parser.add_argument("--env-file", type=Path, help="OPENAI_API_KEY가 있는 환경 파일")
    parser.add_argument("--apply", action="store_true", help="원문 조회와 LLM 추출을 실제 실행")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit는 1 이상이어야 합니다.")
    user_id, conditions, jobs = pending_jobs(
        args.db, args.user_id, include_processed=args.include_processed,
    )
    chosen = jobs[:args.limit] if args.limit else jobs
    print(f"계정: {user_id}; 대상: {len(chosen)}건 / 대기: {len(jobs)}건")
    print("예상 Extractor LLM 호출: 최대", len(chosen), "회; 목록 Searcher·기업 웹 검색: 0회")
    for job in chosen:
        print(f"- {job.company_name} | {job.job_title} | {job.source_url}")
    if not args.apply:
        print("미리보기입니다. 실제 재검증에는 --limit N --apply를 추가하세요.")
        return
    if args.limit is None:
        parser.error("비용 상한을 위해 실제 실행에는 --limit를 지정해야 합니다.")
    if args.env_file is not None:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY가 없습니다. --env-file 또는 환경변수를 설정하세요.")
    os.environ["DATABASE_PATH"] = str(args.db.resolve())
    os.environ["JOB_TRACKER_DB_PATH"] = str(args.db.resolve())
    print("재검증 결과:", dict(reverify(args.db, user_id, conditions, chosen)))


if __name__ == "__main__":
    main()
