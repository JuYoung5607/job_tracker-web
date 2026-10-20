"""점핏의 공개 현재 채용 목록을 Candidate로 변환하는 실험용 어댑터."""

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

import httpx

from nodes.listing_searcher_node import _clear_mismatch_reason
from nodes.page_fetcher import JobPageParser, _check_public_host
from nodes.source_metrics import record_source_request
from schemas.candidate import Candidate, CandidatePageType, SourceType
from schemas.errors import AgentError
from schemas.search import ExperienceLevel, SearchConditions
from state import State


JUMPIT_API_URL = "https://jumpit-api.saramin.co.kr/api/positions"
JUMPIT_POSITION_URL = "https://jumpit.saramin.co.kr/position/"
KOREA_TIME = timezone(timedelta(hours=9))
MAX_RESPONSE_BYTES = 1_000_000
PositionFetcher = Callable[[str, int | None, int], dict]


def make_jumpit_position_fetcher() -> PositionFetcher:
    """고정된 점핏 API 호스트만 조회합니다. 외부 URL은 입력받지 않습니다."""

    def fetch_positions(query: str, career: int | None, page: int) -> dict:
        _check_public_host(JUMPIT_API_URL)
        params: dict[str, str | int] = {
            "keyword": query,
            "sort": "relation",
            "page": page,
        }
        if career is not None:
            params["career"] = career

        with httpx.Client(timeout=15.0, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "GET", JUMPIT_API_URL, params=params,
                headers={"User-Agent": "JobTracker/0.1"},
            ) as response:
                if response.status_code != 200:
                    raise ValueError(f"점핏 목록 HTTP {response.status_code}")
                if "application/json" not in response.headers.get("content-type", "").lower():
                    raise ValueError("점핏 목록 응답이 JSON이 아닙니다.")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("점핏 목록 응답 크기 상한을 넘었습니다.")
                    chunks.append(chunk)

        payload = httpx.Response(200, content=b"".join(chunks)).json()
        if not isinstance(payload, dict) or payload.get("status") != 200:
            raise ValueError("점핏 목록 응답 상태를 확인할 수 없습니다.")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("positions"), list):
            raise ValueError("점핏 목록 응답 구조가 예상과 다릅니다.")
        return result

    return fetch_positions


def _career_filters(conditions: SearchConditions) -> list[int | None]:
    levels = set(conditions.experience_levels)
    if not levels or not levels.issubset({ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR}):
        return [None]
    careers: list[int | None] = [0] if ExperienceLevel.ENTRY in levels else []
    if ExperienceLevel.JUNIOR in levels:
        limit = conditions.max_experience_years
        if limit is None or limit > 5:
            return [None]
        careers.extend(range(1, limit + 1))
    return careers or [None]


def _clean_title(raw: str) -> str:
    # 검색 강조용 <span>은 직무명이 아니므로 기존 HTML 파서를 재사용합니다.
    parser = JobPageParser()
    parser.feed(raw)
    return " ".join(" ".join(parser.text_parts).split())[:200]


def _is_closed(position: dict, now: datetime) -> bool:
    if position.get("alwaysOpen") is True:
        return False
    raw = position.get("closedAt")
    if not isinstance(raw, str) or not raw:
        return False  # 불명확한 마감 정보는 상세 검증으로 넘깁니다.
    try:
        closing = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if closing.tzinfo is None:
        closing = closing.replace(tzinfo=KOREA_TIME)
    return closing < now


def make_jumpit_searcher_node(
    fetch_positions: PositionFetcher, *, broad_first: bool = False,
):
    """검색 예산을 지키며 점핏의 신입·주니어 공고 URL만 발견합니다."""

    def jumpit_searcher_node(state: State) -> dict:
        conditions = state["search_conditions"]
        budget = state["search_budget"]
        deadline = monotonic() + budget.timeout_seconds
        now = datetime.now(KOREA_TIME)
        careers = _career_filters(conditions)
        searches = (
            [(query, None) for query in conditions.target_jobs]
            + [(query, career) for career in careers
               for query in conditions.target_jobs]
            if broad_first else
            [(query, career) for query in conditions.target_jobs
             for career in careers]
        )[:budget.max_search_calls]
        buckets: list[list[Candidate]] = [[] for _ in searches]
        seen: set[str] = set()
        errors: list[AgentError] = []
        calls = 0

        def collect(index: int, page: int) -> int:
            query, career = searches[index]
            print(f"[Jumpit Searcher] 목록 {page}페이지 조회: {query}, 경력 필터={career}")
            discovered_urls: list[str] = []
            try:
                result = fetch_positions(query, career, page)
                positions = result.get("positions")
                if not isinstance(positions, list):
                    raise ValueError("점핏 포지션 목록이 없습니다.")
                filtered: Counter[str] = Counter()
                added = 0
                for item in positions:
                    if not isinstance(item, dict):
                        continue
                    position_id = item.get("id")
                    if type(position_id) is not int or position_id <= 0:
                        continue
                    url = f"{JUMPIT_POSITION_URL}{position_id}"
                    if url in seen:
                        continue
                    raw_title = item.get("title")
                    title = _clean_title(raw_title) if isinstance(raw_title, str) else ""
                    if not title:
                        continue
                    if _is_closed(item, now):
                        filtered["마감일 경과"] += 1
                        continue
                    min_career = item.get("minCareer")
                    max_years = conditions.max_experience_years
                    newcomer = item.get("newcomer") is True
                    if (max_years is not None and isinstance(min_career, int)
                            and min_career > max_years and not newcomer):
                        filtered["경력 상한 초과"] += 1
                        continue
                    reason = _clear_mismatch_reason(title, None, conditions)
                    if reason:
                        filtered[reason] += 1
                        continue
                    seen.add(url)
                    discovered_urls.append(url)
                    buckets[index].append(Candidate(
                        candidate_id=str(uuid5(NAMESPACE_URL, url)),
                        url=url,
                        page_type=CandidatePageType.JOB_DETAIL,
                        source_type=SourceType.JOB_PLATFORM,
                        source_name="Jumpit",
                        discovery_source="Jumpit current listing",
                        discovered_at=datetime.now(timezone.utc),
                        search_query=query,
                        search_track="점핏 현재 채용 목록 직접 탐색",
                        title_hint=title,
                        company_hint=item.get("companyName") if isinstance(item.get("companyName"), str) else None,
                    ))
                    added += 1
                print(f"[Jumpit Searcher] 추가 후보 {added}건, 제외 {dict(filtered)}")
                record_source_request("Jumpit", discovered_urls)
                return added
            except Exception as exc:
                record_source_request("Jumpit", discovered_urls)
                errors.append(AgentError(
                    node_name="jumpit_searcher",
                    error_type=type(exc).__name__,
                    message=f"점핏 목록 조회 실패: 검색어={query}, 경력={career}, 페이지={page}",
                    occurred_at=datetime.now(timezone.utc),
                    retryable=True,
                ))
                print(f"[Jumpit Searcher] 조회 실패: {type(exc).__name__}")
                return 0

        for index in range(len(searches)):
            if monotonic() >= deadline:
                break
            collect(index, 1)
            calls += 1

        page = 2
        while (calls < budget.max_search_calls and monotonic() < deadline
               and len(seen) < budget.max_candidates):
            added = 0
            for index in range(len(searches)):
                if calls >= budget.max_search_calls or monotonic() >= deadline:
                    break
                added += collect(index, page)
                calls += 1
            if added == 0:
                break
            page += 1

        candidates: list[Candidate] = []
        for position in range(max((len(bucket) for bucket in buckets), default=0)):
            for bucket in buckets:
                if position < len(bucket):
                    candidates.append(bucket[position])
                    if len(candidates) >= budget.max_candidates:
                        break
            if len(candidates) >= budget.max_candidates:
                break
        print(f"[Jumpit Searcher] 후보 {len(candidates)}건 선택 (호출 {calls}건)")
        return {"candidates": candidates, "errors": errors}

    return jumpit_searcher_node
