"""One user's ranking request, separate from the collection graph state."""

from typing import NotRequired, Required, TypedDict

from schemas.matching import FeedResult, RankResult
from schemas.profile import PreferenceProfile
from schemas.screening import ScreeningResult
from schemas.search import SearchConditions


class PersonalizationState(TypedDict):
    user_id: Required[str]
    search_conditions: Required[SearchConditions]
    screening_results: Required[list[ScreeningResult]]
    preference_profile: NotRequired[PreferenceProfile | None]
    rank_results: NotRequired[list[RankResult]]
    feed_result: NotRequired[FeedResult | None]
