"""Build and persist a user-specific feed from ranked, screened postings."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from nodes.job_store import SQLiteJobStore
from nodes.ranker_node import _within_explicit_scope
from personalization_state import PersonalizationState
from schemas.matching import FeedItem, FeedResult
from schemas.screening import ScreeningDecision


def _shared_source_key(url: str) -> tuple[str, str] | None:
    """Jumpit and Saramin share posting IDs for the same underlying listing."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host == "jumpit.saramin.co.kr":
        segments = parts.path.rstrip("/").split("/")
        posting_id = segments[-1] if len(segments) >= 3 and segments[-2] == "position" else ""
    elif host in {"saramin.co.kr", "www.saramin.co.kr"}:
        posting_id = parse_qs(parts.query).get("rec_idx", [""])[0]
    else:
        return None
    return ("saramin", posting_id) if posting_id.isdigit() and len(posting_id) >= 7 else None


class SQLiteFeedStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS user_feeds ("
                "user_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, user_id: str) -> FeedResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM user_feeds WHERE user_id = ?", (user_id,)
            ).fetchone()
        return FeedResult.model_validate_json(row[0]) if row else None

    def save(self, feed: FeedResult) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO user_feeds (user_id, payload) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload",
                (feed.user_id, feed.model_dump_json()),
            )


def make_feed_publisher_node(job_store: SQLiteJobStore, feed_store: SQLiteFeedStore):
    def feed_publisher_node(state: PersonalizationState) -> dict:
        user_id = state["user_id"]
        screenings = {str(result.source_url): result
                      for result in state["screening_results"]}
        items: list[FeedItem] = []
        seen_source_keys: set[tuple[str, str]] = set()
        for rank in state.get("rank_results", []):
            if rank.user_id != user_id:
                continue
            job = job_store.get_by_job_id(rank.job_id)
            if job is None:
                continue
            screening = screenings.get(str(job.source_url))
            if (screening is None or screening.decision == ScreeningDecision.EXCLUDED
                    or not _within_explicit_scope(job, screening, state["search_conditions"])):
                continue
            source_key = _shared_source_key(str(job.source_url))
            if source_key is not None and source_key in seen_source_keys:
                continue
            if source_key is not None:
                seen_source_keys.add(source_key)
            items.append(FeedItem(
                job_id=job.job_id, job_version=job.result_version,
                source_url=job.source_url, company_name=job.company_name,
                job_title=job.job_title, score=rank.score, reasons=rank.reasons,
                screening_decision=screening.decision,
                review_reasons=(screening.reasons
                                if screening.decision == ScreeningDecision.REVIEW else []),
                is_exploration=rank.is_exploration,
                preference_profile_version=rank.preference_profile_version,
                ranker_config_version=rank.ranker_config_version,
            ))
        # At most one lower-ranked, in-scope posting gets a visible exploration slot.
        exploration = next((item for item in items if item.is_exploration), None)
        if exploration is not None and len(items) >= 5:
            items.remove(exploration)
            items.insert(4, exploration)
        previous = feed_store.get(user_id)
        if previous is not None and previous.items == items:
            feed = previous
        else:
            feed = FeedResult(
                user_id=user_id, version=previous.version + 1 if previous else 1,
                generated_at=datetime.now(timezone.utc), items=items,
            )
            feed_store.save(feed)
        print(f"[Feed Publisher] 사용자={user_id}, 노출={len(feed.items)}건 "
              f"(검토={sum(item.screening_decision == ScreeningDecision.REVIEW for item in feed.items)}건), "
              f"피드 버전={feed.version}")
        return {"feed_result": feed}
    return feed_publisher_node
