from nodes.feed_publisher_node import SQLiteFeedStore, make_feed_publisher_node
from nodes.job_persister_node import SQLiteJobStore
from nodes.ranker_node import make_ranker_node
from schemas.search import SearchConditions

from .config import settings
from .database import connect


def load_search_conditions(user_id: str) -> SearchConditions | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM user_search_conditions WHERE user_id=?", (user_id,)
        ).fetchone()
    return SearchConditions.model_validate_json(row["payload"]) if row else None


def refresh_user_feed(user_id: str) -> int:
    conditions = load_search_conditions(user_id)
    if conditions is None:
        return 0
    job_store = SQLiteJobStore(settings.database_path)
    screening_results = job_store.list_screenings()
    ranker = make_ranker_node(job_store)
    ranked = ranker({
        "user_id": user_id,
        "search_conditions": conditions,
        "screening_results": screening_results,
        "preference_profile": None,
    })["rank_results"]
    publisher = make_feed_publisher_node(job_store, SQLiteFeedStore(settings.database_path))
    feed = publisher({
        "user_id": user_id,
        "search_conditions": conditions,
        "screening_results": screening_results,
        "preference_profile": None,
        "rank_results": ranked,
    })["feed_result"]
    return len(feed.items)
