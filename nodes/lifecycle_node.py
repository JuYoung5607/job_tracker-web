"""Judge a posting's source lifecycle, independently of user matching."""

import hashlib
import re
from datetime import datetime, timedelta, timezone

from schemas.candidate import AccessStatus, JobDocument, JobPosting, JobPostingDraft
from schemas.lifecycle import LifecycleResult, LifecycleStatus
from schemas.screening import ExtractedPostingFacts, ScreeningDecision, ScreeningResult
from state import State


KOREA_TIME = timezone(timedelta(hours=9))
CLOSED = re.compile(r"마감된\s*공고|(?:모집|채용|접수|지원)\s*(?:마감|종료)|현재\s*모집하지\s*않")
OPEN = re.compile(r"(?:모집|채용|접수|진행)\s*중|D-\d+|채용시")
COMPARABLE_FIELDS = (
    "company_name", "job_title", "employment_type", "experience_level",
    "location", "closes_at",
)
HASH_VERSION = "v2:"
DETAIL_BODY = re.compile(
    r"^(?:잡코리아|사람인|인크루트) 공고 상세 본문: (.*?)\n\n공고 페이지 정보:",
    re.DOTALL,
)


def content_hash(content: str) -> str:
    """Hash only posting detail where available; ignore volatile page counters."""
    match = DETAIL_BODY.search(content)
    posting_text = match.group(1) if match else content
    posting_text = re.sub(r"조회수\s*[\d,]+", "조회수", posting_text)
    normalized = " ".join(posting_text.split())
    return HASH_VERSION + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _comparison_value(field: str, value: object) -> object:
    """Normalize values for comparison only; never alter displayed source data."""
    if field == "employment_type" and isinstance(value, str):
        kinds = tuple(kind for kind in ("정규직", "계약직", "인턴", "파견직", "프리랜서")
                      if kind in value)
        term = re.search(r"(\d+)\s*년\s*후", value)
        if kinds:
            return kinds, term.group(1) if term else None
    if field == "experience_level" and isinstance(value, str):
        compact = " ".join(value.split())
        if "신입" in compact or "경력무관" in compact:
            return "entry_allowed"
        minimum = re.search(r"(?:경력\s*)?(\d+)\s*년\s*이상", compact)
        if minimum:
            return "minimum_years", int(minimum.group(1))
    if field == "location" and isinstance(value, list):
        return tuple(sorted({" ".join(item.split()) for item in value}))
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def _changed_fields(old: JobPosting, draft: JobPostingDraft, new_hash: str) -> list[str]:
    # With identical source text, differing extracted values are parser drift,
    # not an actual change to the posting. This includes company aliases and
    # competing location summaries on the same page.
    if old.content_hash == new_hash or not old.content_hash.startswith(HASH_VERSION):
        return []
    changed = [field for field in COMPARABLE_FIELDS
               if _comparison_value(field, getattr(old, field))
               != _comparison_value(field, getattr(draft, field))]
    # v1 hashes covered the whole platform page and are not comparable to v2.
    if old.content_hash.startswith(HASH_VERSION) and old.content_hash != new_hash:
        changed.append("content_hash")
    return changed


def evaluate_lifecycle(
    document: JobDocument | None,
    facts: ExtractedPostingFacts | None,
    screening: ScreeningResult | None,
    draft: JobPostingDraft | None,
    existing: JobPosting | None,
    *,
    now: datetime | None = None,
    conflicted_fields: set[str] | None = None,
) -> LifecycleResult:
    """Pure rule evaluation; no DB lookup or writes."""
    if now is not None and now.tzinfo is None:
        raise ValueError("now에는 시간대 정보가 필요합니다.")
    current_time = (now or datetime.now(KOREA_TIME)).astimezone(KOREA_TIME)
    previous = existing.lifecycle_status if existing else None
    visible_to_user = bool(
        screening and screening.decision != ScreeningDecision.EXCLUDED
    )

    if document is None or document.access_status != AccessStatus.ACCESSIBLE:
        return LifecycleResult(
            previous_status=previous, next_status=LifecycleStatus.UNAVAILABLE,
            transition_reason="공고 원문에 접근하지 못했습니다. 단발성 실패는 마감 근거가 아닙니다.",
            should_preserve_existing=existing is not None,
        )

    status_text = facts.recruiting_status.value if facts else None
    if (status_text and CLOSED.search(status_text)) or (
        screening and screening.closing_date and screening.closing_date < current_time.date()
    ):
        return LifecycleResult(
            previous_status=previous, next_status=LifecycleStatus.CLOSED,
            transition_reason="명시적 마감 상태 또는 지난 마감일을 확인했습니다.",
            should_preserve_existing=existing is not None,
        )

    has_open_signal = bool(
        (status_text and OPEN.search(status_text))
        or (document.active_application_evidence
            and OPEN.search(document.active_application_evidence))
        or (screening and screening.closing_date
            and screening.closing_date >= current_time.date())
    )
    if not has_open_signal:
        return LifecycleResult(
            previous_status=previous, next_status=LifecycleStatus.UNKNOWN,
            transition_reason="현재 모집 중임을 확인할 근거가 부족합니다.",
            should_preserve_existing=existing is not None,
        )
    if draft is None:
        return LifecycleResult(
            previous_status=previous, next_status=LifecycleStatus.UNKNOWN,
            transition_reason="모집 신호는 있지만 회사명·직무명 초안이 없어 공고를 확정할 수 없습니다.",
            should_preserve_existing=existing is not None,
        )

    if existing is None:
        return LifecycleResult(
            next_status=LifecycleStatus.ACTIVE,
            transition_reason="새 공고에서 현재 모집 근거를 확인했습니다.",
            should_publish=visible_to_user,
        )

    new_hash = content_hash(document.content)
    conflicts = conflicted_fields or set()
    changed = [field for field in _changed_fields(existing, draft, new_hash)
               if field not in conflicts]
    conflict_review = [field for field in COMPARABLE_FIELDS if field in conflicts]
    if changed:
        return LifecycleResult(
            previous_status=previous, next_status=LifecycleStatus.CHANGED,
            transition_reason="기존 공고와 현재 원문 또는 핵심 필드가 달라졌습니다.",
            changed_fields=changed, review_fields=conflict_review,
            should_preserve_existing=bool(conflict_review),
            should_publish=visible_to_user,
        )
    legacy_hash = not existing.content_hash.startswith(HASH_VERSION)
    drift = ([field for field in COMPARABLE_FIELDS
              if getattr(existing, field) != getattr(draft, field)]
             if existing.content_hash == new_hash or legacy_hash else [])
    review_fields = list(dict.fromkeys([*drift, *conflict_review]))
    return LifecycleResult(
        previous_status=previous, next_status=LifecycleStatus.ACTIVE,
        transition_reason=(
            "기존 해시가 구버전이므로 변경 판정을 보류하고 v2 해시로 이전합니다."
            if legacy_hash else
            "원문 근거가 충돌하고 추출값도 달라 기존값을 보존하고 검토 대상으로 기록했습니다."
            if conflict_review and drift else
            "원문의 근거가 충돌해 기존값을 보존하고 검토 대상으로 기록했습니다."
            if conflict_review else
            "원문은 동일하지만 추출값이 달라 기존값을 보존하고 검토 대상으로 기록했습니다."
            if drift else "기존 공고와 동일하며 현재 모집 근거가 있습니다."
        ),
        review_fields=review_fields,
        should_preserve_existing=bool(review_fields),
        should_publish=visible_to_user and previous in {
            LifecycleStatus.CLOSED, LifecycleStatus.UNKNOWN, LifecycleStatus.UNAVAILABLE,
        },
    )


def lifecycle_node(state: State) -> dict:
    existing = state.get("existing_job")
    document = state.get("job_document")
    draft = state.get("job_posting_draft")
    verification = state.get("verification_result")
    result = evaluate_lifecycle(
        document, state.get("extracted_facts"),
        state.get("screening_result"), draft, existing,
        conflicted_fields=set(verification.conflicts) if verification else None,
    )
    if existing and document and draft:
        new_hash = content_hash(document.content)
        print(f"[Lifecycle][비교] 기존 해시={existing.content_hash}, 새 해시={new_hash}")
        conflicts = set(verification.conflicts) if verification else set()
        for field in result.changed_fields + result.review_fields:
            if field == "content_hash":
                continue
            old_value = str(getattr(existing, field))[:180]
            new_value = str(getattr(draft, field))[:180]
            if field in conflicts:
                print(f"[Lifecycle][근거 충돌] {field}: 기존 저장값={old_value!r}, "
                      f"새 추출값={new_value!r}; 기존값 보존, 자동 변경 없음")
            else:
                label = "확정 변경" if field in result.changed_fields else "추출값 차이"
                print(f"[Lifecycle][{label}] {field}: 기존={old_value!r} → 새값={new_value!r}")
    print(
        f"[Lifecycle] 이전={result.previous_status}, 다음={result.next_status}, "
        f"변경={result.changed_fields}, 검토={result.review_fields}, "
        f"기존값 보존={result.should_preserve_existing}; "
        f"{result.transition_reason}"
    )
    return {"lifecycle_result": result}
