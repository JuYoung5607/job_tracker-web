import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from schemas.candidate import Evidence, JobDocument
from schemas.common import VerificationStatus
from schemas.errors import AgentError
from schemas.screening import (
    ExtractedClaim,
    ExtractedPostingFacts,
    RoleRelation,
    ScreeningDecision,
    ScreeningResult,
)
from schemas.search import ExperienceLevel, SearchConditions
from schemas.verification import (
    FieldVerification,
    OverallVerificationStatus,
    VerificationResult,
)
from state import State


KOREA_TIME = timezone(timedelta(hours=9))
DATE_PATTERN = re.compile(r"(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})")
LABELED_DEADLINE = re.compile(r"마감일\s*[:：]?\s*" + DATE_PATTERN.pattern)
APPLICATION_RANGE = re.compile(
    r"(?:접수\s*기간|지원\s*기간|채용\s*기간)\s*[:：]?\s*.{0,90}?"
    + DATE_PATTERN.pattern + r"\s*[~–-]\s*" + DATE_PATTERN.pattern
)
RANGE_PATTERN = re.compile(r"(\d+)\s*(?:년)?\s*[~–-]\s*(\d+)\s*년")
YEAR_PATTERN = re.compile(r"(\d+)\s*년(?!차)")
CLOSED_PATTERN = re.compile(r"마감된\s*(?:포지션|공고)|(?:접수|모집|채용|지원)\s*마감")
OPEN_PATTERN = re.compile(r"(?:모집|접수|채용)\s*중|지원\s*가능|상시\s*(?:채용|모집|접수)")
LEADERSHIP_TITLE = re.compile(r"총괄|센터장|본부장|팀장|시니어|senior|리더(?!십)", re.IGNORECASE)


def _compact(value: str) -> str:
    return " ".join(value.casefold().split())


def _log_excerpt(value: str | None, limit: int = 180) -> str:
    compact = " ".join((value or "").split())
    return repr(compact[:limit] + ("…" if len(compact) > limit else ""))


def _quote_origin(document: JobDocument, quote: str | None) -> str:
    if not quote or "공고 페이지 정보:" not in document.content:
        return "미확인"
    detail, parent = document.content.split("공고 페이지 정보:", 1)
    in_detail = _compact(quote) in _compact(detail)
    in_parent = _compact(quote) in _compact(parent)
    if in_detail and in_parent:
        return "상세+외부"
    if in_detail:
        return "상세"
    if in_parent:
        return "외부"
    return "미확인"


def _conflicting_required_years(document: JobDocument, minimum_years: int | None,
                                max_years: int | None) -> int | None:
    """상세 본문의 명시적 필수 경력이 추출된 0년 판정과 충돌하면 재검토합니다."""
    if (minimum_years != 0 or max_years is None
            or not document.content.startswith("사람인 공고 상세 본문:")
            or "공고 페이지 정보:" not in document.content):
        return None
    detail = document.content.split("공고 페이지 정보:", 1)[0]
    for match in re.finditer(r"(?:실무\s*)?경력\s*(\d+)\s*년\s*이상", detail):
        years = int(match.group(1))
        before = detail[max(0, match.start() - 16):match.start()]
        after = detail[match.end():match.end() + 18]
        if years > max_years and "신입" not in before and not re.search(r"우대|선호", after):
            return years
    return None


def _common_experience_scope(document: JobDocument, quote: str | None,
                             position_titles: list[str]) -> str | None:
    """상세 본문에서 모든 직무에 공통 적용된다고 명시된 경력 문구만 찾습니다."""
    if not quote or "공고 페이지 정보:" not in document.content:
        return None
    detail = document.content.split("공고 페이지 정보:", 1)[0]
    quote_at = detail.find(quote)
    if quote_at < 0:
        return None
    prefix = detail[max(0, quote_at - 500):quote_at]
    common_markers = list(re.finditer(
        r"(?:모든|전체|\d+개)\s*(?:모집\s*)?(?:직무|포지션)\s*공통|"
        r"(?:지원\s*자격|경력\s*조건).{0,50}공통",
        prefix,
    ))
    if not common_markers:
        return None
    marker = common_markers[-1]
    scope = detail[max(0, quote_at - 500) + marker.start():quote_at + len(quote)]
    if any(title and title in scope for title in position_titles):
        return None
    return scope


def _verified_quote(
    facts: ExtractedPostingFacts,
    field_name: str,
    evidence: list[Evidence],
    document: JobDocument,
) -> str | None:
    quote = getattr(facts, field_name).quote
    if not quote:
        return None
    if not any(item.field == field_name and item.quote == quote for item in evidence):
        return None
    if _compact(quote) not in _compact(document.content + " " + document.page_title):
        return None
    return quote


def parse_closing_date(quote: str | None) -> date | None:
    if not quote:
        return None
    if not re.search(r"마감|접수|지원|채용\s*기간|종료|까지", quote):
        return None
    matches = list(DATE_PATTERN.finditer(quote))
    if not matches:
        return None
    # 접수기간의 첫 날짜는 시작일이다. 끝 날짜가 없으면 마감일을 추측하지 않는다.
    if len(matches) == 1 and re.search(r"접수\s*기간|지원\s*기간|채용\s*기간", quote):
        before_date = quote[:matches[0].start()]
        after_date = quote[matches[0].end():]
        if not re.search(r"마감|종료|까지", before_date + after_date):
            return None
    match = matches[-1]
    if "채용시" in quote and len(matches) == 1 and not re.search(r"마감일\s*[:：]?\s*20", quote):
        return None
    try:
        return date(*(int(group) for group in match.groups()))
    except ValueError:
        return None


def _source_deadlines(document: JobDocument) -> dict[date, str]:
    """Find explicitly labeled application deadlines, not arbitrary page dates."""
    found: dict[date, str] = {}
    for pattern in (LABELED_DEADLINE, APPLICATION_RANGE):
        for match in pattern.finditer(document.content):
            quote = match.group(0)
            parsed = parse_closing_date(quote)
            if parsed is not None:
                found.setdefault(parsed, quote)
    return found


def parse_minimum_years(quote: str | None) -> int | None:
    if not quote:
        return None
    # 학력의 4년(제)을 경력 연수로 읽지 않는다.
    career_text = re.sub(r"(?:대학교?졸업|대졸|대학교|4년제\s*대학)\s*\(?\s*4년\s*\)?\s*(?:이상)?", "", quote)
    career_text = re.sub(r"4\s*년제", "", career_text)
    career_text = re.split(r"우대사항", career_text, maxsplit=1)[0]
    if re.search(r"(?:경력|실무).{0,80}우대", career_text):
        return None
    if re.search(r"(?:신입|주니어|경력\s*\d+\s*년\s*(?:이상|이하|미만)?)\s*(?:지원자)?\s*(?:우대|선호)", career_text):
        return None
    compact = re.sub(r"\s+", "", career_text)
    if "경력무관" in compact or "경력제한없음" in compact:
        return 0
    if re.search(r"신입\s*(?:또는|및|[/·,~–-])\s*(?:AI\s*관련\s*실무\s*)?경력", career_text):
        return 0
    # '신입~10년'의 10년은 상한이지 최소 요구 경력이 아니다.
    if re.search(r"신입\s*[~–-]\s*(?:경력\s*)?\d+\s*년", career_text):
        return 0
    match = RANGE_PATTERN.search(career_text)
    if match:
        lower, upper = (int(value) for value in match.groups())
        return lower if lower <= upper else None
    match = YEAR_PATTERN.search(career_text)
    if match:
        following = career_text[match.end():match.end() + 4]
        if "이하" in following or "미만" in following or "이내" in following:
            return 0
        return int(match.group(1))
    if "신입" in career_text:
        return 0
    return None


def experience_basis(quote: str | None) -> str | None:
    """연수 숫자가 없더라도 명시적인 지원 자격은 별도 기록합니다."""
    if not quote:
        return None
    if parse_minimum_years(quote) is not None:
        return "minimum_years"
    if (re.search(r"주니어|junior", quote, re.IGNORECASE)
            and not re.search(r"(?:주니어|junior)\s*(?:우대|선호)", quote, re.IGNORECASE)):
        return "junior_without_years"
    return None


def assess_screening(
    experience_quote: str | None,
    deadline_quote: str | None,
    status_quote: str | None,
    conditions: SearchConditions,
    as_of: date,
    application_open: bool = False,
) -> tuple[ScreeningDecision, list[str], date | None, int | None]:
    """확인된 원문 표현만으로 마감과 요구 경력을 판정합니다."""
    reasons: list[str] = []
    closing_date = parse_closing_date(deadline_quote)
    minimum_years = parse_minimum_years(experience_quote)
    status_text = status_quote or ""

    if CLOSED_PATTERN.search(status_text) or (
        closing_date is not None and closing_date < as_of
    ):
        deadline_ok = False
        reasons.append("마감된 공고로 확인됐습니다.")
    elif ((closing_date is not None and closing_date >= as_of)
          or OPEN_PATTERN.search(status_text) or application_open):
        deadline_ok = True
        if application_open and closing_date is None and not OPEN_PATTERN.search(status_text):
            reasons.append("채용시 표시와 홈페이지 지원 버튼을 확인했습니다.")
    else:
        deadline_ok = None
        reasons.append("현재 모집 중인지 확인할 근거가 없습니다.")

    selected_levels = set(conditions.experience_levels)
    entry_junior = {ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR}
    if not selected_levels or not selected_levels.issubset(entry_junior):
        experience_ok = True
    elif not experience_quote:
        experience_ok = None
        reasons.append("요구 경력을 원문에서 확인하지 못했습니다.")
    else:
        max_years = conditions.max_experience_years
        if minimum_years is not None and max_years is not None and minimum_years > max_years:
            experience_ok = False
            reasons.append(f"최소 요구 경력 {minimum_years}년이 설정 상한 {max_years}년을 초과합니다.")
        elif ExperienceLevel.JUNIOR not in selected_levels and minimum_years is not None and minimum_years > 0:
            experience_ok = False
            reasons.append("신입만 찾는 조건에 경력 요구 공고입니다.")
        elif experience_basis(experience_quote) is not None:
            experience_ok = True
        else:
            experience_ok = None
            reasons.append("경력 조건의 의미가 불분명합니다.")

    if deadline_ok is False or experience_ok is False:
        decision = ScreeningDecision.EXCLUDED
    elif deadline_ok is True and experience_ok is True:
        decision = ScreeningDecision.ELIGIBLE
    else:
        decision = ScreeningDecision.REVIEW
    return decision, reasons, closing_date, minimum_years


def verifier_node(state: State) -> dict:
    document = state["job_document"]
    candidate = state["current_candidate"]
    facts = state["extracted_facts"]
    if document is None or candidate is None or facts is None:
        raise ValueError("Verifier에는 추출된 공고와 Candidate가 필요합니다.")

    evidence = list(state.get("evidence", []))
    application_evidence = None
    application_status = document.active_application_evidence
    incruit_open = (application_status == "채용시"
                    and urlsplit(str(document.source_url)).hostname == "job.incruit.com")
    saramin_open = (application_status is not None
                    and urlsplit(str(document.source_url)).hostname == "www.saramin.co.kr"
                    and (re.fullmatch(r"D-\d+|오늘\s*마감|채용시\s*마감", application_status) is not None)
                    and f"({application_status})" in document.page_title)
    if incruit_open or saramin_open:
        application_evidence = Evidence(
            evidence_id=str(uuid5(
                NAMESPACE_URL,
                f"{document.source_url}|application_status|{application_status}",
            )),
            field="recruiting_status",
            quote=application_status,
            source_url=document.source_url,
            source_section=("page_title" if saramin_open else "application_widget"),
        )
        evidence.append(application_evidence)
    experience_quote = _verified_quote(facts, "experience_requirement", evidence, document)
    deadline_quote = _verified_quote(facts, "deadline", evidence, document)
    status_quote = _verified_quote(facts, "recruiting_status", evidence, document)
    source_deadlines = _source_deadlines(document)
    extracted_date = parse_closing_date(deadline_quote)
    deadline_conflict = bool(
        len(source_deadlines) > 1
        or (extracted_date is not None and source_deadlines
            and extracted_date not in source_deadlines)
        or (re.search(r"채용시\s*마감", (deadline_quote or "") + " " + (status_quote or ""))
            and source_deadlines)
    )
    if len(source_deadlines) == 1 and not deadline_conflict:
        source_date, source_quote = next(iter(source_deadlines.items()))
        if extracted_date is None:
            deadline_quote = source_quote
            evidence.append(Evidence(
                evidence_id=str(uuid5(NAMESPACE_URL,
                                      f"{document.source_url}|deadline|{source_quote}")),
                field="deadline", quote=source_quote,
                source_url=document.source_url, source_section="page_text",
            ))
            print(f"[Verifier][마감일 보완] 원문 표기={source_quote!r}, 날짜={source_date}")
    if deadline_conflict:
        print(f"[Verifier][마감일 충돌] 추출={deadline_quote!r}, 원문 날짜={list(source_deadlines)}")
    print(
        f"[Verifier][근거] url={document.source_url} "
        f"경력_원본={_log_excerpt(facts.experience_requirement.quote)} "
        f"경력_확인={_log_excerpt(experience_quote)} "
        f"경력_위치={_quote_origin(document, experience_quote)} "
        f"경력_파싱={parse_minimum_years(experience_quote)} "
        f"경력_근거={experience_basis(experience_quote)} "
        f"마감={_log_excerpt(deadline_quote)} "
        f"상태={_log_excerpt(status_quote)} "
        f"지원표시={_log_excerpt(application_status)} "
        f"복수포지션={facts.is_multi_position}"
    )
    decision, reasons, closing_date, minimum_years = assess_screening(
        experience_quote,
        deadline_quote,
        status_quote,
        state["search_conditions"],
        datetime.now(KOREA_TIME).date(),
        application_open=application_evidence is not None,
    )

    chosen_position = None
    chosen_experience_quote = None
    chosen_experience_value = None
    position_evidence_ids: list[str] = []
    common_claim = facts.common_experience_requirement
    common_quote = common_claim.quote if common_claim else None
    common_scope = facts.common_experience_scope_quote
    common_verified = bool(common_quote and facts.common_experience_scope_quote
                           and any(item.field == "common:experience_requirement"
                                   and item.quote == common_quote for item in evidence)
                           and any(item.field == "common:scope"
                                   and item.quote == common_scope
                                   for item in evidence))
    if facts.is_multi_position and not common_verified:
        derived_scope = _common_experience_scope(
            document, experience_quote,
            [position.job_title.value or "" for position in facts.positions],
        )
        if derived_scope:
            common_quote = experience_quote
            common_scope = derived_scope
            common_claim = ExtractedClaim(value=experience_quote, quote=experience_quote)
            for field, quote in (
                ("common:experience_requirement", common_quote),
                ("common:scope", common_scope),
            ):
                evidence.append(Evidence(
                    evidence_id=str(uuid5(NAMESPACE_URL, f"{document.source_url}|{field}|{quote}")),
                    field=field, quote=quote, source_url=document.source_url,
                    source_section="page_text",
                ))
            common_verified = True
            print("[Verifier][공통경력] 상세 본문의 명시적 공통 지원 자격에서 보강")
    if facts.is_multi_position:
        print(
            f"[Verifier][공통경력] 인용={_log_excerpt(common_quote)} "
            f"공통구간={_log_excerpt(common_scope)} "
            f"검증={common_verified}"
        )
    if facts.is_multi_position:
        position_results = []
        for index, position in enumerate(facts.positions):
            related = position.role_relation == RoleRelation.RELATED
            if not related:
                continue
            prefix = f"position:{index}:"
            title_verified = any(item.field == prefix + "job_title"
                                 and item.quote == position.job_title.quote for item in evidence)
            role_verified = any(item.field == prefix + "role_relation"
                                and item.quote == position.role_evidence for item in evidence)
            scope_verified = any(item.field == prefix + "scope"
                                 and item.quote == position.scope_quote for item in evidence)
            exp_quote = position.experience_requirement.quote
            exp_verified = bool(exp_quote and any(
                item.field == prefix + "experience_requirement" and item.quote == exp_quote
                for item in evidence
            ))
            use_common = bool(common_verified and (not exp_quote or exp_quote == common_quote)
                              and not scope_verified)
            effective_quote = (exp_quote if exp_verified and scope_verified
                               else common_quote if use_common else None)
            print(
                f"[Verifier][포지션 {index}] 직무={_log_excerpt(position.job_title.value)} "
                f"관련성={position.role_relation.value} "
                f"경력={_log_excerpt(exp_quote)} "
                f"직무구간={scope_verified} 공통경력적용={use_common} "
                f"사용경력={_log_excerpt(effective_quote)}"
            )
            if not title_verified or not role_verified:
                continue
            position_decision, position_reasons, _, position_years = assess_screening(
                effective_quote,
                deadline_quote, status_quote, state["search_conditions"],
                datetime.now(KOREA_TIME).date(),
                application_open=application_evidence is not None,
            )
            if not (scope_verified or use_common) and position_decision != ScreeningDecision.EXCLUDED:
                position_decision = ScreeningDecision.REVIEW
                position_reasons.append("직무와 경력이 같은 포지션에 속하는지 확인하지 못했습니다.")
            levels_for_position = set(state["search_conditions"].experience_levels)
            if (levels_for_position
                    and levels_for_position.issubset({ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR})
                    and LEADERSHIP_TITLE.search(position.job_title.value or "")):
                position_decision = ScreeningDecision.EXCLUDED
                position_reasons.append("모집 직책이 신입·주니어 대상보다 상위 수준입니다.")
            position_results.append((position_decision, position_reasons,
                                     position_years, index, position, effective_quote))
        if position_results:
            selected = next((item for item in position_results
                             if item[0] == ScreeningDecision.ELIGIBLE), None)
            if selected is None:
                selected = next((item for item in position_results
                                 if item[0] == ScreeningDecision.REVIEW), position_results[0])
            decision, reasons, minimum_years, index, chosen_position, chosen_experience_quote = selected
            chosen_experience_value = (common_claim.value if common_verified
                                       and chosen_experience_quote == common_quote
                                       and not chosen_position.experience_requirement.quote
                                       else chosen_position.experience_requirement.value)
            if (decision == ScreeningDecision.EXCLUDED
                    and not (closing_date is not None
                             and closing_date < datetime.now(KOREA_TIME).date())
                    and not CLOSED_PATTERN.search(status_quote or "")
                    and any(position.role_relation == RoleRelation.UNCERTAIN
                            for position in facts.positions)):
                decision = ScreeningDecision.REVIEW
                reasons = [*reasons, "다른 포지션의 직무 관련성이 확인되지 않았습니다."]
            position_evidence_ids = [item.evidence_id for item in evidence
                                     if item.field.startswith(f"position:{index}:")]
            if chosen_experience_quote == common_quote and common_verified:
                position_evidence_ids.extend(item.evidence_id for item in evidence
                                             if item.field.startswith("common:"))
            reasons = [f"선택 포지션: {chosen_position.job_title.value}", *reasons]
        elif facts.positions and all(position.role_relation == RoleRelation.UNRELATED
                                     for position in facts.positions):
            decision = ScreeningDecision.EXCLUDED
            reasons = ["공고의 모집 포지션 모두 목표 직무와 다릅니다."]
        else:
            decision = ScreeningDecision.REVIEW
            reasons = ["관련 포지션의 직무·경력 근거를 확인하지 못했습니다."]
    if ((closing_date is not None and closing_date < datetime.now(KOREA_TIME).date())
            or CLOSED_PATTERN.search(status_quote or "")):
        decision = ScreeningDecision.EXCLUDED
        if "마감된 공고로 확인됐습니다." not in reasons:
            reasons.append("마감된 공고로 확인됐습니다.")
    if saramin_open and closing_date is None and not CLOSED_PATTERN.search(status_quote or ""):
        reasons = [reason for reason in reasons
                   if reason != "채용시 표시와 홈페이지 지원 버튼을 확인했습니다."]
        reasons.append(f"사람인 공고 제목의 {application_status} 표시를 확인했습니다.")

    if deadline_conflict:
        if decision == ScreeningDecision.ELIGIBLE:
            decision = ScreeningDecision.REVIEW
        reasons = [reason for reason in reasons
                   if reason != "모집 상태·요구 경력·직무가 현재 검색 조건에 맞습니다."]
        reasons.append("원문의 마감일 표기가 서로 충돌해 확인이 필요합니다.")
        closing_date = None

    role_evidence = next(
        (item for item in evidence if item.field == "role_relation"
         and item.quote == facts.role_evidence),
        None,
    )
    print(
        f"[Verifier] 직무 판단: url={document.source_url}, "
        f"job_title={facts.job_title.value!r}, "
        f"role_relation={facts.role_relation.value}, "
        f"role_evidence={facts.role_evidence!r}, "
        f"근거 등록={role_evidence is not None}"
    )
    if facts.is_multi_position:
        if chosen_position is not None:
            print(f"[Verifier] 복수 포지션 중 선택: {chosen_position.job_title.value!r}")
    elif role_evidence is None or facts.role_relation == RoleRelation.UNCERTAIN:
        if decision == ScreeningDecision.ELIGIBLE:
            decision = ScreeningDecision.REVIEW
        reasons.append("모집 직무가 목표 직무와 관련되는지 확인이 필요합니다.")
    elif facts.role_relation == RoleRelation.UNRELATED:
        decision = ScreeningDecision.EXCLUDED
        reasons.append("실제 모집 직무가 목표 직무와 다릅니다.")
    elif decision == ScreeningDecision.ELIGIBLE:
        reasons.append("모집 상태·요구 경력·직무가 현재 검색 조건에 맞습니다.")

    levels = set(state["search_conditions"].experience_levels)
    title = (chosen_position.job_title.value if chosen_position else facts.job_title.value) or ""
    if (levels and levels.issubset({ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR})
            and LEADERSHIP_TITLE.search(title)):
        decision = ScreeningDecision.EXCLUDED
        reasons = [reason for reason in reasons if reason != "모집 상태·요구 경력·직무가 현재 검색 조건에 맞습니다."]
        reasons.append("모집 직책이 신입·주니어 대상보다 상위 수준입니다.")

    selected_scope_verified = (chosen_position is None or any(
        item.field.endswith(":scope") and item.evidence_id in position_evidence_ids
        for item in evidence
    ))
    selected_experience_quote = (chosen_experience_quote
                                 if chosen_position else experience_quote)
    selected_experience_basis = (experience_basis(selected_experience_quote)
                                 if selected_scope_verified else None)
    conflicting_years = None if facts.is_multi_position else _conflicting_required_years(
        document, minimum_years, state["search_conditions"].max_experience_years,
    )
    if conflicting_years is not None:
        print(f"[Verifier][경력충돌] 상세 본문에 경력 {conflicting_years}년 이상 표시")
        if decision == ScreeningDecision.ELIGIBLE:
            decision = ScreeningDecision.REVIEW
        reasons.append("상세 본문의 필수 경력과 추출된 신입 조건이 충돌합니다.")

    field_results = [
        FieldVerification(
            field="job_title",
            extracted_value=title or None,
            status=(VerificationStatus.VERIFIED if chosen_position or role_evidence
                    else VerificationStatus.MISSING),
            evidence_ids=(position_evidence_ids if chosen_position else
                          [role_evidence.evidence_id] if role_evidence else []),
        ),
        FieldVerification(
            field="experience_level",
            extracted_value=(chosen_experience_value if chosen_position
                             else facts.experience_requirement.value if experience_quote else None),
            status=(VerificationStatus.VERIFIED if selected_experience_basis is not None
                    else VerificationStatus.MISSING),
            evidence_ids=(position_evidence_ids if chosen_position else
                          [item.evidence_id for item in evidence if item.field == "experience_requirement"]),
        ),
        FieldVerification(
            field="closes_at",
            extracted_value=closing_date.isoformat() if closing_date else None,
            status=(VerificationStatus.CONFLICT if deadline_conflict else
                    VerificationStatus.VERIFIED if closing_date or status_quote or application_evidence
                    else VerificationStatus.MISSING),
            evidence_ids=[item.evidence_id for item in evidence if item.field in {"deadline", "recruiting_status"}],
            reason=("원문의 마감일 표기가 서로 충돌합니다." if deadline_conflict else ""),
        ),
    ]
    missing = [item.field for item in field_results if item.status == VerificationStatus.MISSING]
    verification = VerificationResult(
        field_results=field_results,
        overall_status=(OverallVerificationStatus.VERIFIED if not missing and not deadline_conflict
                        else OverallVerificationStatus.PARTIAL),
        critical_missing_fields=missing,
        conflicts=["closes_at"] if deadline_conflict else [],
    )
    screening = ScreeningResult(
        candidate_id=candidate.candidate_id,
        source_url=document.source_url,
        decision=decision,
        reasons=reasons,
        closing_date=closing_date,
        minimum_experience_years=minimum_years,
        experience_basis=selected_experience_basis,
        evidence_ids=[*position_evidence_ids, *[item.evidence_id for item in evidence if item.field in {
            "experience_requirement", "deadline", "recruiting_status", "role_relation"
        }]],
    )
    draft = state.get("job_posting_draft")
    if draft is None and screening.decision == ScreeningDecision.ELIGIBLE:
        screening = screening.model_copy(update={
            "decision": ScreeningDecision.REVIEW,
            "reasons": [*screening.reasons, "회사명 또는 직무명을 원문에서 확인하지 못했습니다."],
        })
    if draft is not None and chosen_position is not None:
        draft = draft.model_copy(update={
            "job_title": chosen_position.job_title.value,
            "experience_level": chosen_experience_value,
        })
    if draft is not None and closing_date is not None:
        draft = draft.model_copy(update={"closes_at": datetime.combine(
            closing_date, time.max, tzinfo=KOREA_TIME
        )})
    elif draft is not None and deadline_conflict:
        draft = draft.model_copy(update={"closes_at": None})
    print(
        f"[Verifier][최종] 판정={screening.decision.value}, "
        f"최소경력={screening.minimum_experience_years}, "
        f"경력근거={screening.experience_basis}, "
        f"이유={screening.reasons}"
    )
    return {
        "verification_result": verification,
        "screening_result": screening,
        "job_posting_draft": draft,
        "evidence": evidence,
        "candidate_processing_complete": True,
    }
