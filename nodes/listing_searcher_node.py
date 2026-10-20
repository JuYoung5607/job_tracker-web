"""웹 검색 없이 채용 플랫폼의 현재 검색 목록에서 후보 URL을 발견합니다."""

import re
from collections import Counter
from datetime import datetime, timezone
from time import monotonic
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from nodes.navigator_node import PageFetcher
from nodes.source_metrics import record_source_request
from nodes.saramin_adapter import (
    fetch_saramin_listing_page,
    saramin_detail_url,
    saramin_search_url,
)
from schemas.candidate import Candidate, CandidatePageType, SourceType
from schemas.errors import AgentError
from schemas.search import ExperienceLevel, SearchConditions
from state import State


JOBKOREA_DETAIL_PATH = re.compile(r"^/Recruit/GI_Read/\d+/?$", re.IGNORECASE)
INCRUIT_DETAIL_PATH = re.compile(r"^/jobdb_info/jobpost\.asp$", re.IGNORECASE)
LEADERSHIP_TITLE = re.compile(r"총괄|센터장|본부장|팀장|시니어|senior|리더(?!십)", re.IGNORECASE)
EXPERIENCE_YEARS = re.compile(r"경력\s*[(（]?\s*(\d+)\s*년?", re.IGNORECASE)
EXPERIENCE_ONLY = re.compile(r"경력자\s*전용|경력직\s*전용")


def _experience_terms(conditions: SearchConditions) -> list[str]:
    levels = set(conditions.experience_levels)
    if not levels or not levels.issubset({ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR}):
        return []
    terms = []
    if ExperienceLevel.ENTRY in levels:
        terms.append("신입")
    if ExperienceLevel.JUNIOR in levels:
        terms.append("주니어")
    return terms


def _jobkorea_detail_url(list_url: str, href: str) -> str | None:
    absolute = urljoin(list_url, href)
    parts = urlsplit(absolute)
    if parts.scheme != "https" or parts.hostname not in {
        "jobkorea.co.kr", "www.jobkorea.co.kr", "m.jobkorea.co.kr"
    }:
        return None
    if not JOBKOREA_DETAIL_PATH.fullmatch(parts.path):
        return None
    # 현재 검색 목록의 일반 결과(sc=630)만 사용합니다. 추천·광고 영역(sc=808)은
    # 검색어와 무관한 공고가 섞일 수 있어 이 실험에서 제외합니다.
    if parse_qs(parts.query).get("sc") != ["630"]:
        return None
    return f"https://www.jobkorea.co.kr{parts.path.rstrip('/')}"


def _incruit_detail_url(list_url: str, href: str) -> str | None:
    parts = urlsplit(urljoin(list_url, href))
    if (parts.scheme != "https" or parts.hostname != "job.incruit.com"
            or not INCRUIT_DETAIL_PATH.fullmatch(parts.path)):
        return None
    job_ids = parse_qs(parts.query).get("job", [])
    if len(job_ids) != 1 or not job_ids[0].isdigit():
        return None
    return f"https://job.incruit.com/jobdb_info/jobpost.asp?job={job_ids[0]}"


def _clear_mismatch_reason(title: str, context: str | None, conditions: SearchConditions) -> str | None:
    """신입·주니어에게 명백히 맞지 않는 제목/목록 경력만 조기 제외합니다."""
    levels = set(conditions.experience_levels)
    if not levels or not levels.issubset({ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR}):
        return None
    if LEADERSHIP_TITLE.search(title):
        return "상위 직책 제목"
    if EXPERIENCE_ONLY.search(title):
        return "경력자 전용 제목"
    if levels == {ExperienceLevel.ENTRY} and re.search(r"경력사원|경력직", title) and "신입" not in title:
        return "신입 제외 제목"

    # '신입~경력 8년'은 신입도 지원할 수 있으므로 제외하지 않습니다.
    max_years = conditions.max_experience_years
    if max_years is not None:
        # 제목과 목록 카드의 경력 표기가 충돌해도 명시적인 제목 조건을 놓치지 않습니다.
        for text in (title, context or ""):
            if "신입" in text or "경력무관" in text.replace(" ", ""):
                continue
            match = EXPERIENCE_YEARS.search(text)
            if match:
                following = text[match.end():match.end() + 5]
                if "이하" not in following and int(match.group(1)) > max_years:
                    return f"최소 경력 {match.group(1)}년"
    return None


def make_listing_searcher_node(fetch_page: PageFetcher):
    """JobKorea·Incruit·Saramin 현재 목록에서 상세 URL을 읽는 실험용 노드."""

    def listing_searcher_node(state: State) -> dict:
        conditions = state["search_conditions"]
        budget = state["search_budget"]
        deadline = monotonic() + budget.timeout_seconds
        seen_urls: set[str] = set()
        errors: list[AgentError] = []
        terms = _experience_terms(conditions)

        searches = []
        for target_job in conditions.target_jobs:
            jobkorea_query = " ".join([target_job, *terms])
            searches.append((
                "JobKorea", jobkorea_query,
                "https://www.jobkorea.co.kr/Search?" + urlencode({
                    "Page_No": 1, "stext": jobkorea_query,
                }), _jobkorea_detail_url,
            ))
            # 인크루트는 검색어에 '신입 주니어'를 덧붙이지 않고 사이트의
            # 신입 필터를 사용합니다. 이 필터는 경력무관 공고도 포함합니다.
            incruit_params = {"col": "job_all", "il": "y", "kw": target_job, "page": 1}
            if terms:
                incruit_params["crr"] = 1
            searches.append((
                "Incruit", target_job,
                "https://job.incruit.com/jobdb_list/searchjob.asp?" + urlencode(incruit_params),
                _incruit_detail_url,
            ))
            searches.append((
                "Saramin", target_job,
                saramin_search_url(target_job, 1, conditions),
                saramin_detail_url,
            ))

        active_searches = searches[:budget.max_search_calls]
        search_buckets: list[list[Candidate]] = [[] for _ in active_searches]

        def collect(index: int, page_number: int) -> int:
            source_name, query, first_url, detail_url = active_searches[index]
            parts = urlsplit(first_url)
            params = parse_qs(parts.query)
            page_key = {"JobKorea": "Page_No", "Incruit": "page", "Saramin": "recruitPage"}[source_name]
            params[page_key] = [str(page_number)]
            list_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params, doseq=True), ""))
            print(f"[Listing Searcher] {source_name} 목록 {page_number}페이지 조회: {query}")
            discovered_urls: list[str] = []
            try:
                page = (fetch_saramin_listing_page(list_url)
                        if source_name == "Saramin" else fetch_page(list_url))
                bucket = search_buckets[index]
                filtered_reasons: Counter[str] = Counter()
                added = 0
                for link in page.links:
                    url = detail_url(page.url, link.url)
                    if url is None or url in seen_urls:
                        continue
                    title = " ".join(link.text.split())
                    if not title:
                        continue
                    reason = _clear_mismatch_reason(
                        title, link.context if source_name in {"Incruit", "Saramin"} else None,
                        conditions,
                    )
                    if reason:
                        filtered_reasons[reason] += 1
                        continue
                    seen_urls.add(url)
                    discovered_urls.append(url)
                    bucket.append(Candidate(
                        candidate_id=str(uuid5(NAMESPACE_URL, url)),
                        url=url,
                        page_type=CandidatePageType.JOB_DETAIL,
                        source_type=SourceType.JOB_PLATFORM,
                        source_name=source_name,
                        discovery_source=f"{source_name} current listing",
                        discovered_at=datetime.now(timezone.utc),
                        search_query=query,
                        search_track="현재 채용 목록 직접 탐색",
                        title_hint=title[:200],
                        parent_candidate_id=str(uuid5(NAMESPACE_URL, page.url)),
                        discovery_depth=1,
                    ))
                    added += 1
                print(
                    f"[Listing Searcher] {source_name} 추가 후보 {added}건, "
                    f"명백한 부적합 {sum(filtered_reasons.values())}건 제외 "
                    f"({dict(filtered_reasons)})"
                )
                record_source_request(source_name, discovered_urls)
                return added
            except Exception as exc:
                record_source_request(source_name, discovered_urls)
                errors.append(AgentError(
                    node_name="listing_searcher",
                    error_type=type(exc).__name__,
                    message=f"목록 조회 실패: {list_url}",
                    occurred_at=datetime.now(timezone.utc),
                    retryable=True,
                ))
                print(f"[Listing Searcher] 목록 조회 실패: {type(exc).__name__}")
                return 0

        calls = 0
        for index in range(len(active_searches)):
            if monotonic() >= deadline:
                break
            collect(index, 1)
            calls += 1

        # 첫 페이지의 유효 후보가 부족할 때만 다음 페이지를 순차적으로 탐색합니다.
        page_number = 2
        while (calls < budget.max_search_calls and monotonic() < deadline
               and len(seen_urls) < budget.max_candidates):
            added_this_round = 0
            for index in range(len(active_searches)):
                if calls >= budget.max_search_calls or monotonic() >= deadline:
                    break
                added_this_round += collect(index, page_number)
                calls += 1
                if len(seen_urls) >= budget.max_candidates:
                    break
            if added_this_round == 0:
                break
            page_number += 1

        buckets = [bucket for bucket in search_buckets if bucket]
        candidates: list[Candidate] = []
        for position in range(max((len(bucket) for bucket in buckets), default=0)):
            for bucket in buckets:
                if position < len(bucket):
                    candidates.append(bucket[position])
                    if len(candidates) >= budget.max_candidates:
                        break
            if len(candidates) >= budget.max_candidates:
                break
        print(f"[Listing Searcher] 후보 {len(candidates)}건 선택")
        return {"candidates": candidates, "errors": errors}

    return listing_searcher_node
