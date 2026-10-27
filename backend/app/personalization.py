from nodes.feed_publisher_node import SQLiteFeedStore, make_feed_publisher_node
from nodes.job_store import SQLiteJobStore
from nodes.job_store import canonical_url
from nodes.ranker_node import make_ranker_node
from schemas.search import SearchConditions
from schemas.screening import ScreeningResult

from .config import settings
from .database import connect


def load_search_conditions(user_id: str) -> SearchConditions | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM user_search_conditions WHERE user_id=?", (user_id,)
        ).fetchone()
    return SearchConditions.model_validate_json(row["payload"]) if row else None


def load_screenings_for_user(user_id: str, job_store: SQLiteJobStore) -> list[ScreeningResult]:
    """Use a user's current verdict over an imported/shared fallback verdict."""
    by_url = {canonical_url(str(item.source_url)): item
              for item in job_store.list_screenings()}
    with connect() as connection:
        rows = connection.execute(
            "SELECT canonical_url,payload FROM user_screening_results WHERE user_id=?",
            (user_id,),
        ).fetchall()
    for row in rows:
        by_url[row["canonical_url"]] = ScreeningResult.model_validate_json(row["payload"])
    return list(by_url.values())


def refresh_user_feed(user_id: str) -> int:
    from .preference_updater import load_preference_profile

    conditions = load_search_conditions(user_id)
    if conditions is None:
        return 0
    job_store = SQLiteJobStore(settings.database_path)
    job_store.close_past_deadline()
    screening_results = load_screenings_for_user(user_id, job_store)
    profile = load_preference_profile(user_id)
    ranker = make_ranker_node(job_store)
    ranked = ranker({
        "user_id": user_id,
        "search_conditions": conditions,
        "screening_results": screening_results,
        "preference_profile": profile,
    })["rank_results"]
    publisher = make_feed_publisher_node(job_store, SQLiteFeedStore(settings.database_path))
    feed = publisher({
        "user_id": user_id,
        "search_conditions": conditions,
        "screening_results": screening_results,
        "preference_profile": profile,
        "rank_results": ranked,
    })["feed_result"]
    return len(feed.items)
