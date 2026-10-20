# nodes/candidate_iterator.py

from schemas.candidate import Candidate
from state import State


CANDIDATE_FIELDS_TO_CLEAR = {
    "job_document": None,
    "extracted_facts": None,
    "job_posting_draft": None,
    "evidence": [],
    "company_profile": None,
    "verification_result": None,
    "screening_result": None,
    "existing_job": None,
    "lifecycle_result": None,
    "persisted_job_id": None,
    "discovered_candidates": [],
    "candidate_processing_complete": False,
}


def _merge_candidates(
    existing: list[Candidate],
    discovered: list[Candidate],
    max_candidates: int,
) -> list[Candidate]:
    """새 URL을 중복 없이 큐 뒤에 추가합니다."""
    result = list(existing)
    seen_urls = {str(candidate.url) for candidate in result}

    for candidate in discovered:
        if len(result) >= max_candidates:
            break

        url = str(candidate.url)
        if url in seen_urls:
            continue

        result.append(candidate)
        seen_urls.add(url)

    return result


def candidate_iterator_node(state: State) -> dict:
    """처리 완료된 후보를 넘기고 다음 후보 하나를 선택합니다."""
    budget = state["search_budget"]
    candidates = list(state.get("candidates", []))
    cursor = state.get("candidate_cursor", 0)

    if state.get("candidate_processing_complete", False):
        candidates = _merge_candidates(
            existing=candidates,
            discovered=state.get("discovered_candidates", []),
            max_candidates=budget.max_candidates,
        )
        cursor += 1

    # 아직 처리 중인데 Iterator가 다시 호출되는 것은 그래프 연결 오류입니다.
    elif state.get("current_candidate") is not None:
        raise RuntimeError(
            "현재 후보의 처리가 끝나기 전에 Iterator가 다시 호출됐습니다."
        )

    update = {
        "candidates": candidates,
        "candidate_cursor": cursor,
        "current_candidate": (
            candidates[cursor] if cursor < len(candidates) else None
        ),
        **CANDIDATE_FIELDS_TO_CLEAR,
    }
    return update
