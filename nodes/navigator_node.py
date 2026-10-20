# nodes/navigator_node.py

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urldefrag, urljoin, urlsplit
from uuid import NAMESPACE_URL, uuid5

from schemas.candidate import (
    AccessStatus,
    Candidate,
    JobDocument,
)
from schemas.errors import AgentError
from schemas.screening import ScreeningDecision, ScreeningResult
from node_trace import trace_detail
from state import State


@dataclass
class PageLink:
    url: str
    text: str
    context: str | None = None


@dataclass
class FetchedPage:
    url: str
    title: str
    content: str
    links: list[PageLink]
    http_status: int

    # HTML을 읽는 fetcher가 채워주는 페이지 구조 정보
    has_job_posting_schema: bool = False
    has_single_posting_heading: bool = False
    has_application_action: bool = False
    active_application_evidence: str | None = None
    iframe_sources: list[str] | None = None
    embedded_detail_url: str | None = None


PageFetcher = Callable[[str], FetchedPage]


class DetailContentUnavailable(ValueError):
    """공고 상세 내용을 확인할 수 없어 사람의 검토가 필요한 경우."""

CLOSED_POSTING_PATTERN = re.compile(
    r"마감된\s*(?:공고|포지션)|"
    r"(?:모집|채용|접수|지원)\s*(?:이|가)?\s*종료되었|"
    r"현재\s*모집하지\s*않"
)


def _incruit_job_id(url: str, path: str) -> str | None:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "job.incruit.com"
            or parts.port not in (None, 443) or parts.username or parts.password
            or parts.path.lower() != path):
        return None
    values = parse_qs(parts.query).get("job", [])
    return values[0] if len(values) == 1 and values[0].isdigit() else None


def _incruit_detail_frame(page: FetchedPage) -> str | None:
    job_id = _incruit_job_id(page.url, "/jobdb_info/jobpost.asp")
    if job_id is None:
        return None
    for source in page.iframe_sources or []:
        frame_url = urljoin(page.url, source)
        if _incruit_job_id(frame_url, "/s_common/jobpost/jobpostcont.asp") == job_id:
            return frame_url
    return None


def _jobkorea_id(url: str, *, detail: bool = False) -> str | None:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in {"www.jobkorea.co.kr", "jobkorea.co.kr"}
            or parts.port not in (None, 443) or parts.username or parts.password):
        return None
    if detail:
        if parts.path.lower() != "/recruit/gi_read_comt_ifrm":
            return None
        values = parse_qs(parts.query).get("Gno", [])
        return values[0] if len(values) == 1 and values[0].isdigit() else None
    match = re.fullmatch(r"/Recruit/GI_Read/(\d+)", parts.path, re.IGNORECASE)
    return match.group(1) if match else None


def is_detail_page(page: FetchedPage) -> bool:
    """직무명이 아니라 페이지 구조로 개별 공고 여부를 판단합니다."""
    if not page.content.strip():
        return False

    if page.has_job_posting_schema:
        return True

    return (
        page.has_single_posting_heading
        and (
            page.has_application_action
            or bool(CLOSED_POSTING_PATTERN.search(page.content))
        )
    )


def find_detail_links(
    parent: Candidate,
    page: FetchedPage,
    limit: int = 10,
) -> list[Candidate]:
    """목록 페이지에 실제로 있는 링크를 다음 탐색 후보로 만듭니다."""
    if parent.discovery_depth >= 1:
        return []

    parent_host = urlsplit(page.url).netloc.lower()
    parent_url = urldefrag(page.url)[0]
    found: list[Candidate] = []
    seen_urls: set[str] = set()

    for link in page.links:
        if len(found) >= limit:
            break

        url = urldefrag(urljoin(page.url, link.url))[0]
        parts = urlsplit(url)

        if parts.scheme not in {"http", "https"}:
            continue
        if parts.netloc.lower() != parent_host:
            continue
        if url == parent_url or url in seen_urls:
            continue

        # 링크 문구가 채용공고로 이어진다는 단서가 있어야 합니다.
        # 특정 직무명은 검사하지 않습니다.
        label = link.text.strip().lower()
        if not any(
            word in label
            for word in (
                "채용", "모집", "지원",
                "job", "position", "opening", "apply",
            )
        ):
            continue

        seen_urls.add(url)
        found.append(
            Candidate(
                candidate_id=str(uuid5(NAMESPACE_URL, url)),
                url=url,
                page_type="unknown",
                source_type=parent.source_type,
                source_name=parent.source_name,
                discovery_source="listing_page",
                discovered_at=datetime.now(timezone.utc),
                search_query=parent.search_query,
                search_track=parent.search_track,
                title_hint=link.text.strip()[:200] or None,
                parent_candidate_id=parent.candidate_id,
                discovery_depth=1,
            )
        )

    return found


def make_navigator_node(fetch_page: PageFetcher):
    from nodes.saramin_adapter import saramin_content_url, saramin_detail_id

    def navigator_node(state: State) -> dict:
        candidate = state.get("current_candidate")
        if candidate is None:
            raise ValueError("current_candidate가 없습니다.")

        try:
            page = fetch_page(str(candidate.url))
            content_preview = " ".join(page.content.split())[:160]
            print(
                f"[Navigator] HTTP {page.http_status}, 제목={page.title[:100]!r}, "
                f"본문={len(page.content)}자, 링크={len(page.links)}건"
            )
            print(
                "[Navigator] 상세 판별 신호: "
                f"JobPosting={page.has_job_posting_schema}, "
                f"단일 h1={page.has_single_posting_heading}, "
                f"지원 버튼/링크={page.has_application_action}, "
                f"마감 상태 문구={bool(CLOSED_POSTING_PATTERN.search(page.content))}"
            )
            print(f"[Navigator] 본문 앞부분: {content_preview!r}")
            trace_detail("기본 페이지 HTML → 텍스트", {
                "url": page.url, "http_status": page.http_status,
                "title": page.title, "text_chars": len(page.content),
                "text_preview": page.content[:500],
                "links": len(page.links), "embedded_detail_url": page.embedded_detail_url,
                "iframe_sources": page.iframe_sources,
            })

            # 상세 공고라는 구조적 근거가 있으면 본문을 다음 단계로 전달합니다.
            if (is_detail_page(page) or _incruit_job_id(page.url, "/jobdb_info/jobpost.asp")
                    or _jobkorea_id(page.url) or saramin_detail_id(page.url)):
                document_content = page.content
                frame_url = _incruit_detail_frame(page)
                if (_incruit_job_id(page.url, "/jobdb_info/jobpost.asp")
                        and not frame_url):
                    raise DetailContentUnavailable("인크루트 상세 iframe 주소를 확인하지 못했습니다.")
                if frame_url:
                    print(f"[Navigator] 인크루트 상세 iframe 조회: {frame_url}")
                    try:
                        frame = fetch_page(frame_url)
                    except Exception as exc:
                        raise DetailContentUnavailable("인크루트 상세 iframe에 접근하지 못했습니다.") from exc
                    if (_incruit_job_id(frame.url, "/s_common/jobpost/jobpostcont.asp")
                            != _incruit_job_id(page.url, "/jobdb_info/jobpost.asp")
                            or not frame.content.strip()
                            or frame.content.strip() == frame.title.strip()):
                        raise DetailContentUnavailable("인크루트 상세 iframe 본문을 확인하지 못했습니다.")
                    document_content = (
                        f"인크루트 공고 상세 본문: {frame.content}\n\n"
                        f"공고 페이지 정보: {page.content}"
                    )
                    trace_detail("인크루트 iframe 병합", {
                        "detail_url": frame.url, "detail_text_chars": len(frame.content),
                        "merge_order": "상세 본문 → 기본 공고 페이지 정보",
                    })
                    print(f"[Navigator] 인크루트 상세 본문 {len(frame.content)}자 병합")
                jobkorea_id = _jobkorea_id(page.url)
                if jobkorea_id:
                    detail_url = page.embedded_detail_url
                    if not detail_url or _jobkorea_id(detail_url, detail=True) != jobkorea_id:
                        raise DetailContentUnavailable("잡코리아 상세 본문 주소를 확인하지 못했습니다.")
                    print(f"[Navigator] 잡코리아 상세 본문 조회: {detail_url}")
                    try:
                        detail = fetch_page(detail_url)
                    except Exception as exc:
                        raise DetailContentUnavailable("잡코리아 상세 본문에 접근하지 못했습니다.") from exc
                    if (_jobkorea_id(detail.url, detail=True) != jobkorea_id
                            or not detail.content.strip()):
                        raise DetailContentUnavailable("잡코리아 상세 본문을 확인하지 못했습니다.")
                    document_content = (
                        f"잡코리아 공고 상세 본문: {detail.content}\n\n"
                        f"공고 페이지 정보: {page.content}"
                    )
                    trace_detail("잡코리아 상세 본문 병합", {
                        "detail_url": detail.url, "detail_text_chars": len(detail.content),
                        "merge_order": "상세 본문 → 기본 공고 페이지 정보",
                    })
                    print(f"[Navigator] 잡코리아 상세 본문 {len(detail.content)}자 병합")
                saramin_id = saramin_detail_id(page.url)
                if saramin_id:
                    detail_url = saramin_content_url(page.url)
                    print(f"[Navigator] 사람인 상세 본문 조회: {detail_url}")
                    try:
                        detail = fetch_page(detail_url)
                    except Exception as exc:
                        raise DetailContentUnavailable("사람인 상세 본문에 접근하지 못했습니다.") from exc
                    if (saramin_detail_id(detail.url, content=True) != saramin_id
                            or not detail.content.strip()
                            or detail.content.strip() == detail.title.strip()):
                        raise DetailContentUnavailable("사람인 상세 본문을 확인하지 못했습니다.")
                    document_content = (
                        f"사람인 공고 상세 본문: {detail.content}\n\n"
                        f"공고 페이지 정보: {page.content}"
                    )
                    trace_detail("사람인 상세 본문 병합", {
                        "detail_url": detail.url, "detail_text_chars": len(detail.content),
                        "merge_order": "상세 본문 → 기본 공고 페이지 정보",
                    })
                    print(f"[Navigator] 사람인 상세 본문 {len(detail.content)}자 병합")
                print("[Navigator] 개별 공고로 분류 → Extractor")
                document = JobDocument(
                    document_id=str(uuid5(NAMESPACE_URL, page.url)),
                    candidate_id=candidate.candidate_id,
                    source_url=page.url,
                    page_title=page.title,
                    content=document_content,
                    fetched_at=datetime.now(timezone.utc),
                    http_status=page.http_status,
                    is_official=(
                        candidate.source_type.value == "official"
                    ),
                    access_status=AccessStatus.ACCESSIBLE,
                    active_application_evidence=page.active_application_evidence,
                )
                return {
                    "job_document": document,
                    "discovered_candidates": [],
                    "candidate_processing_complete": False,
                }

            # 상세 공고가 아니면 목록에서 새 URL을 찾습니다.
            missing_signals = []
            if not page.content.strip():
                missing_signals.append("본문 없음")
            if not page.has_job_posting_schema:
                missing_signals.append("JobPosting 없음")
            if not page.has_single_posting_heading:
                missing_signals.append("단일 h1 없음")
            if not page.has_application_action and not CLOSED_POSTING_PATTERN.search(page.content):
                missing_signals.append("지원 버튼/링크·마감 상태 문구 없음")
            print(f"[Navigator] 개별 공고 미확정: {', '.join(missing_signals)}")
            discovered = find_detail_links(candidate, page)
            if discovered:
                print(
                    f"[Navigator] 링크 단서 {len(discovered)}건 발견 → "
                    "Candidate Iterator에 추가 요청"
                )
                for item in discovered[:3]:
                    print(f"[Navigator] 발견 URL: {item.url}")
                return {
                    "job_document": None,
                    "discovered_candidates": discovered,
                    "candidate_processing_complete": True,
                }

            # 어느 쪽도 확인할 수 없다면 추측하지 않습니다.
            print("[Navigator] 공고 유형 불명·추가 링크 없음 → 다음 후보")
            return {
                "job_document": None,
                "discovered_candidates": [],
                "candidate_processing_complete": True,
                "errors": [
                    AgentError(
                        node_name="navigator",
                        error_type="UnclassifiedPage",
                        message=f"페이지 유형을 확인할 수 없습니다: {candidate.url}",
                        occurred_at=datetime.now(timezone.utc),
                        retryable=False,
                    )
                ],
            }

        except DetailContentUnavailable as exc:
            print(f"[Navigator] 상세 본문 미확인 → review: {exc}")
            return {
                "job_document": None,
                "discovered_candidates": [],
                "candidate_processing_complete": True,
                "screening_result": ScreeningResult(
                    candidate_id=candidate.candidate_id,
                    source_url=candidate.url,
                    decision=ScreeningDecision.REVIEW,
                    reasons=[str(exc)],
                ),
            }

        except Exception as exc:
            return {
                "job_document": None,
                "discovered_candidates": [],
                "candidate_processing_complete": True,
                "errors": [
                    AgentError(
                        node_name="navigator",
                        error_type=type(exc).__name__,
                        message=f"페이지 처리 실패: {candidate.url}-{exc}",
                        occurred_at=datetime.now(timezone.utc),
                        retryable=False,
                    )
                ],
            }

    return navigator_node
