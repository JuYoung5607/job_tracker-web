"""Import previously stored jobs for a local web preview without network calls.

Archived screening decisions were not persisted by the old pipeline. Imported
postings therefore receive REVIEW, never ELIGIBLE, until the current pipeline
checks them again.
"""

import argparse
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from nodes.job_store import SQLiteJobStore, canonical_url
from schemas.candidate import JobPosting
from schemas.lifecycle import LifecycleStatus
from schemas.screening import ScreeningDecision, ScreeningResult


KOREA_TIME = timezone(timedelta(hours=9))


def _read_jobs(path: Path) -> list[str]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute("SELECT payload FROM jobs")]
    finally:
        connection.close()


def _existing_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        return {row[0] for row in connection.execute("SELECT canonical_url FROM jobs")} if found else set()
    finally:
        connection.close()


def import_jobs(source: Path, target: Path, *, apply: bool = False) -> Counter:
    if source.resolve() == target.resolve():
        raise ValueError("원본 DB와 웹 DB는 서로 다른 파일이어야 합니다.")
    if not source.is_file():
        raise FileNotFoundError(source)

    existing = _existing_urls(target)
    now = datetime.now(timezone.utc)
    counts: Counter = Counter()
    store = SQLiteJobStore(target) if apply else None
    for payload in _read_jobs(source):
        try:
            job = JobPosting.model_validate_json(payload)
        except ValueError:
            counts["invalid"] += 1
            continue
        if job.lifecycle_status not in {LifecycleStatus.ACTIVE, LifecycleStatus.CHANGED}:
            counts["not_open"] += 1
            continue
        if job.closes_at is not None:
            closes_at = job.closes_at
            if closes_at.tzinfo is None:
                closes_at = closes_at.replace(tzinfo=KOREA_TIME)
            if closes_at.astimezone(timezone.utc) < now:
                counts["expired"] += 1
                continue
        key = canonical_url(str(job.source_url))
        if key in existing:
            counts["already_present"] += 1
            continue
        counts["importable"] += 1
        if store is None:
            continue
        store.save(job)
        store.save_screening(ScreeningResult(
            candidate_id=str(uuid5(NAMESPACE_URL, key)),
            source_url=job.source_url,
            decision=ScreeningDecision.REVIEW,
            reasons=["이전 DB에 원문 검증 결과가 없어 모집 상태·직무·경력을 다시 확인해야 합니다."],
            closing_date=job.closes_at.date() if job.closes_at else None,
        ))
        existing.add(key)
        counts["imported"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="기존 프로젝트의 jobs.sqlite3")
    parser.add_argument("--target", type=Path, default=Path("data/jobs.sqlite3"),
                        help="웹 프로젝트의 jobs.sqlite3")
    parser.add_argument("--apply", action="store_true", help="생략하면 건수만 확인합니다")
    args = parser.parse_args()
    counts = import_jobs(args.source, args.target, apply=args.apply)
    print("가져오기 결과:", dict(counts))
    if not args.apply:
        print("미리보기입니다. 실제 반영하려면 --apply를 추가하세요.")
        return
    os.environ["DATABASE_PATH"] = str(args.target.resolve())
    from backend.app.database import connect, initialize_database
    from backend.app.personalization import refresh_user_feed

    initialize_database(args.target)
    with connect() as connection:
        users = [row[0] for row in connection.execute("SELECT user_id FROM user_search_conditions")]
    for user_id in users:
        print(f"사용자 {user_id}: 피드 {refresh_user_feed(user_id)}건")


if __name__ == "__main__":
    main()
