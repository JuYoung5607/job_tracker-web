"""Record a readable, candidate-by-candidate pipeline trace."""

import json
import os
from functools import wraps
from pathlib import Path
from textwrap import fill


def _short(value, limit=220):
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _claim(claim):
    if claim is None:
        return None
    return {"value": _short(claim.value), "quote": _short(claim.quote)}


def _text_path() -> Path | None:
    path = os.getenv("JOB_TRACKER_TRACE_FILE")
    return Path(path) if path else None


def trace_detail(title, value):
    """Write an intermediate transformation inside the active node section."""
    path = _text_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (value if isinstance(value, str) else
                json.dumps(value, ensure_ascii=False, indent=2, default=str))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"  [{title}]\n")
        for line in rendered.splitlines() or [""]:
            stream.write(f"    {line}\n")


def _readable_input(name, state):
    candidate = state.get("current_candidate")
    if name == "existing_job_lookup_node":
        return f"기존 공고 조회 URL: {candidate.url if candidate else '없음'}"
    if name == "navigator_node":
        return f"받은 Candidate URL: {candidate.url if candidate else '없음'}"
    if name == "extractor_node":
        document = state.get("job_document")
        if document:
            return (f"받은 JobDocument: {document.source_url}\n"
                    f"제목: {document.page_title}\n본문: {len(document.content)}자; "
                    "아래 Navigator의 '최종 추출 원문'을 사용")
    if name == "company_enricher_node":
        draft = state.get("job_posting_draft")
        facts = state.get("extracted_facts")
        return (f"받은 JobPostingDraft: 회사={draft.company_name}, 직무={draft.job_title}"
                if draft else
                f"받은 JobPostingDraft: 없음; 추출 회사명="
                f"{facts.company_name.value if facts else None}, "
                f"직무명={facts.job_title.value if facts else None}")
    if name == "verifier_node":
        facts = state.get("extracted_facts")
        return (f"받은 추출 결과: 회사={facts.company_name.value if facts else None}, "
                f"직무={facts.job_title.value if facts else None}; "
                f"근거 {len(state.get('evidence') or [])}건")
    if name == "lifecycle_node":
        screening = state.get("screening_result")
        existing = state.get("existing_job")
        document = state.get("job_document")
        return (f"받은 공고 원문: {'있음' if document else '없음'}; "
                f"사용자 조건 판정={screening.decision.value if screening else '없음'}; "
                f"기존 저장 공고={'있음' if existing else '없음'}")
    if name == "candidate_iterator_node":
        return (f"후보 커서={state.get('candidate_cursor', 0)}, "
                f"후보 총 {len(state.get('candidates') or [])}건")
    if name == "ranker_node":
        return (f"사용자={state['user_id']}; 검증 결과={len(state['screening_results'])}건; "
                f"선호 프로필={'있음' if state.get('preference_profile') else '없음'}")
    if name == "feed_publisher_node":
        return f"사용자={state['user_id']}; 랭킹 결과={len(state.get('rank_results') or [])}건"
    return f"받은 State 키: {', '.join(sorted(state))}"


def _readable_output(name, update):
    if name == "navigator_node":
        document = update.get("job_document")
        if document:
            return (f"넘긴 JobDocument: 제목={document.page_title}; "
                    f"본문={len(document.content)}자; URL={document.source_url}")
        return "JobDocument 없음 → Extractor로 전달하지 않음"
    if name == "extractor_node":
        facts = update.get("extracted_facts")
        draft = update.get("job_posting_draft")
        return (f"확인된 추출: 회사={facts.company_name.value if facts else None}, "
                f"직무={facts.job_title.value if facts else None}\n"
                f"JobPostingDraft: {'생성' if draft else '없음'}\n"
                f"근거: {len(update.get('evidence') or [])}건")
    if name == "company_enricher_node":
        profile = update.get("company_profile")
        if not profile:
            return "CompanyProfile 없음"
        lines = [f"CompanyProfile 참고 정보: 업종={profile.industry}, "
                 f"규모={profile.company_size}, 본사={profile.headquarters}"]
        lines.extend(
            f"- {item.field}: {item.value} | 출처: {item.source_url} "
            f"| 확인 상태: {item.verification_status.value}"
            for item in profile.company_evidence
            if item.field != "company_name"
        )
        return "\n".join(lines)
    if name == "verifier_node":
        result = update.get("screening_result")
        return (f"판정={result.decision}; 이유={'; '.join(result.reasons)}"
                if result else "판정 없음")
    if name == "lifecycle_node":
        result = update.get("lifecycle_result")
        return (f"공고 상태={result.next_status.value}; 변경 필드={result.changed_fields}; "
                f"검토 필드={result.review_fields}; "
                f"기존값 보존={result.should_preserve_existing}; 이유={result.transition_reason}"
                if result else "공고 상태 판정 없음")
    if name == "existing_job_lookup_node":
        existing = update.get("existing_job")
        return (f"기존 공고: ID={existing.job_id}, 버전={existing.result_version}, "
                f"상태={existing.lifecycle_status.value}" if existing else "기존 공고 없음")
    if name == "job_persister_node":
        return f"저장된 공고 ID: {update.get('persisted_job_id') or '없음'}"
    if name == "candidate_iterator_node":
        candidate = update.get("current_candidate")
        return f"다음 Candidate: {candidate.url if candidate else '없음'}"
    if name == "ranker_node":
        return f"사용자별 순위 결과 {len(update.get('rank_results') or [])}건"
    if name == "feed_publisher_node":
        feed = update.get("feed_result")
        return (f"사용자 피드 버전={feed.version}; 노출={len(feed.items)}건"
                if feed else "사용자 피드 없음")
    if "candidates" in update:
        return f"발견 후보 {len(update['candidates'] or [])}건"
    return f"반환한 키: {', '.join(sorted(update))}"


def trace_node(name, node):
    path = os.getenv("JOB_TRACKER_TRACE_FILE")
    if not path:
        return node
    trace_path = Path(path)

    @wraps(node)
    def traced(state):
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        candidate = state.get("current_candidate")
        with trace_path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n{'=' * 72}\n{name} | {candidate.url if candidate else '실행 공통'}\n")
            stream.write(_readable_input(name, state) + "\n")
        try:
            update = node(state)
            if name == "navigator_node" and update.get("job_document"):
                content = update["job_document"].content
                trace_detail("최종 추출 원문 (HTML 태그 제거·상세 본문 병합 후)",
                             fill(content, width=120, break_long_words=False))
            if name == "extractor_node":
                facts = update.get("extracted_facts")
                if facts:
                    trace_detail("원문 대조 후 확정한 필드", {
                        key: _claim(getattr(facts, key)) for key in (
                            "company_name", "job_title", "experience_requirement",
                            "deadline", "recruiting_status", "employment_type", "location")
                    })
            trace_detail("다음 노드로 넘긴 정보", _readable_output(name, update))
            return update
        except Exception as exc:
            trace_detail("노드 예외", f"{type(exc).__name__}: {exc}")
            raise

    return traced
