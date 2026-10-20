"""플랫폼별 후보 수집기를 한 검색 예산 아래에서 결합합니다."""

from collections.abc import Callable
from time import monotonic

from schemas.candidate import Candidate
from state import State


SearcherNode = Callable[[State], dict]


def _merge_candidates(
    listing: list[Candidate], jumpit: list[Candidate], limit: int,
) -> list[Candidate]:
    """세 HTML 목록 경로와 점핏 경로를 3:1로 섞어 한 출처의 독점을 막습니다."""
    result: list[Candidate] = []
    seen: set[str] = set()
    listing_index = jumpit_index = 0
    while len(result) < limit and (listing_index < len(listing) or jumpit_index < len(jumpit)):
        batch = [*listing[listing_index:listing_index + 3],
                 *jumpit[jumpit_index:jumpit_index + 1]]
        listing_index += 3
        jumpit_index += 1
        for candidate in batch:
            url = str(candidate.url)
            if url not in seen:
                result.append(candidate)
                seen.add(url)
                if len(result) >= limit:
                    break
    return result


def make_combined_listing_searcher_node(
    listing_searcher: SearcherNode, jumpit_searcher: SearcherNode,
):
    def combined_listing_searcher_node(state: State) -> dict:
        budget = state["search_budget"]
        target_count = len(state["search_conditions"].target_jobs)
        # 각 직무의 세 HTML 플랫폼에 한 번씩, 점핏에는 남은 호출을 예약합니다.
        listing_calls = min(3 * target_count, max(1, budget.max_search_calls - target_count))
        jumpit_calls = budget.max_search_calls - listing_calls
        deadline = monotonic() + budget.timeout_seconds
        print(f"[Listing Searcher] 호출 예산: HTML 목록 {listing_calls}회, 점핏 {jumpit_calls}회")

        listing_state = dict(state)
        listing_state["search_budget"] = budget.model_copy(update={
            "max_search_calls": listing_calls,
        })
        listing_result = listing_searcher(listing_state)

        jumpit_result: dict = {"candidates": [], "errors": []}
        remaining = deadline - monotonic()
        if jumpit_calls and remaining > 0:
            jumpit_state = dict(state)
            jumpit_state["search_budget"] = budget.model_copy(update={
                "max_search_calls": jumpit_calls,
                "timeout_seconds": max(1, int(remaining)),
            })
            jumpit_result = jumpit_searcher(jumpit_state)

        candidates = _merge_candidates(
            listing_result.get("candidates", []),
            jumpit_result.get("candidates", []),
            budget.max_candidates,
        )
        print(f"[Listing Searcher] 네 플랫폼 통합 후보 {len(candidates)}건")
        return {
            "candidates": candidates,
            "errors": [*listing_result.get("errors", []),
                       *jumpit_result.get("errors", [])],
        }

    return combined_listing_searcher_node
