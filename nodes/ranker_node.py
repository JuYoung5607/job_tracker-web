"""Deterministic, user-specific ranking; no LLM or shared-job mutations."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from nodes.job_persister_node import SQLiteJobStore
from personalization_state import PersonalizationState
from schemas.candidate import JobPosting
from schemas.lifecycle import LifecycleStatus
from schemas.matching import RankResult
from schemas.profile import PreferenceProfile
from schemas.screening import ScreeningDecision, ScreeningResult
from schemas.search import ExperienceLevel, SearchConditions


VISIBLE_STATUSES = {LifecycleStatus.ACTIVE, LifecycleStatus.CHANGED}


@dataclass(frozen=True)
class RankerConfig:
    version: str = "mvp-v1"
    base_score: float = 0.32
    role_weight: float = 0.23
    skill_weight: float = 0.10
    location_weight: float = 0.07
    experience_weight: float = 0.06
    employment_weight: float = 0.05
    preference_cap: float = 0.12
    review_penalty: float = 0.10
    exploration_min_items: int = 5


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[가-힣]+", value.casefold()))


def _role_relevance(title: str, targets: list[str]) -> float:
    title_terms = _terms(title)
    return max((len(title_terms & _terms(target)) / len(_terms(target))
                for target in targets if _terms(target)), default=0.0)


def _matches(value: str, choices: list[str]) -> bool:
    lowered = value.casefold()
    return any(choice.casefold() in lowered or lowered in choice.casefold()
               for choice in choices if choice.strip())


def _within_explicit_scope(job: JobPosting, screening: ScreeningResult,
                           conditions: SearchConditions) -> bool:
    if screening.decision == ScreeningDecision.EXCLUDED:
        return False
    if job.lifecycle_status not in VISIBLE_STATUSES:
        return False
    if (conditions.max_experience_years is not None
            and screening.minimum_experience_years is not None
            and screening.minimum_experience_years > conditions.max_experience_years):
        return False
    if (set(conditions.experience_levels) == {ExperienceLevel.ENTRY}
            and screening.minimum_experience_years is not None
            and screening.minimum_experience_years > 0):
        return False
    if conditions.locations and job.location and not any(
        _matches(place, conditions.locations) for place in job.location
    ):
        return False
    # A possible future conversion ("계약직 (정규직 전환 가능)") is not a
    # present full-time offer. Compare only the stated employment category.
    employment_category = re.split(r"[（(]", job.employment_type or "", maxsplit=1)[0]
    employment_category = re.sub(r"정규직\s*전환\s*(?:가능|심사)?", "", employment_category)
    if (conditions.employment_types and job.employment_type
            and not _matches(employment_category, conditions.employment_types)):
        return False
    excluded_text = " ".join([
        job.job_title, job.employment_type or "", *job.responsibilities,
    ]).casefold()
    if any(term.strip().casefold() in excluded_text
           for term in conditions.exclusion_conditions if term.strip()):
        return False
    return True


def _preference_adjustment(job: JobPosting, profile: PreferenceProfile | None,
                           config: RankerConfig
                           ) -> tuple[float, list[str]]:
    if profile is None:
        return 0.0, []
    searchable = {
        "company": job.company_name,
        "job": job.job_title,
        "skill": " ".join([job.job_title, *job.requirements, *job.responsibilities]),
        "location": " ".join(job.location),
        "employment_type": job.employment_type or "",
    }
    total = 0.0
    reasons = []
    for feature in profile.features:
        source = searchable.get(feature.category)
        if not source or feature.value.casefold() not in source.casefold():
            continue
        observations = feature.positive_count + feature.negative_count
        contribution = feature.weight * feature.confidence * min(1.0, observations / 3)
        total += contribution
        if contribution:
            direction = "선호" if contribution > 0 else "비선호"
            reasons.append(
                f"누적 {direction}: {feature.category}={feature.value} "
                f"(가중치 {feature.weight:+.2f}, 신뢰도 {feature.confidence:.2f})"
            )
    return max(-config.preference_cap,
               min(config.preference_cap, total * config.preference_cap)), reasons[:3]


def _score(job: JobPosting, screening: ScreeningResult,
           conditions: SearchConditions, profile: PreferenceProfile | None,
           config: RankerConfig,
           ) -> tuple[float, list[str]]:
    role = _role_relevance(job.job_title, conditions.target_jobs)
    all_text = " ".join([job.job_title, *job.requirements, *job.responsibilities])
    skill = (sum(1 for item in conditions.skills if item.casefold() in all_text.casefold())
             / len(conditions.skills) if conditions.skills else 0.0)
    location = bool(conditions.locations and job.location and any(
        _matches(place, conditions.locations) for place in job.location
    ))
    employment_category = re.split(r"[（(]", job.employment_type or "", maxsplit=1)[0]
    employment_category = re.sub(r"정규직\s*전환\s*(?:가능|심사)?", "", employment_category)
    employment = bool(conditions.employment_types and job.employment_type
                      and _matches(employment_category, conditions.employment_types))
    experience = screening.minimum_experience_years is not None
    adjustment, preference_reasons = _preference_adjustment(job, profile, config)
    score = (config.base_score + config.role_weight * role
             + config.skill_weight * skill + config.location_weight * location
             + config.experience_weight * experience
             + config.employment_weight * employment + adjustment)
    if screening.decision == ScreeningDecision.REVIEW:
        score -= config.review_penalty
    reasons = []
    if role:
        reasons.append("직무명이 검색 직무와 관련됩니다.")
    if skill:
        reasons.append("요청한 기술이 공고에 나타납니다.")
    if location:
        reasons.append("요청한 근무지역에 해당합니다.")
    if experience:
        reasons.append("요구 경력을 확인했습니다.")
    reasons.extend(preference_reasons)
    if screening.decision == ScreeningDecision.REVIEW:
        reasons.append("검토 필요: " + "; ".join(screening.reasons[:2]))
    return round(max(0.0, min(1.0, score)), 4), reasons or ["확인된 공고를 기준으로 정렬했습니다."]


def make_ranker_node(store: SQLiteJobStore, config: RankerConfig | None = None):
    config = config or RankerConfig()
    def ranker_node(state: PersonalizationState) -> dict:
        user_id = state["user_id"]
        conditions = state["search_conditions"]
        profile = state.get("preference_profile")
        if profile is not None and profile.user_id != user_id:
            raise ValueError("다른 사용자의 PreferenceProfile은 사용할 수 없습니다.")
        latest_by_url = {str(item.source_url): item
                         for item in state["screening_results"]}
        ranked: list[RankResult] = []
        ranked_at = datetime.now(timezone.utc)
        for screening in latest_by_url.values():
            if screening.decision == ScreeningDecision.EXCLUDED:
                continue
            job = store.get(str(screening.source_url))
            if job is None or not _within_explicit_scope(job, screening, conditions):
                continue
            score, reasons = _score(job, screening, conditions, profile, config)
            ranked.append(RankResult(
                user_id=user_id, job_id=job.job_id, score=score, reasons=reasons,
                preference_profile_version=profile.version if profile else 1,
                ranker_config_version=config.version,
                ranked_at=ranked_at,
            ))
        ranked.sort(key=lambda result: (-result.score, result.job_id))
        if len(ranked) >= config.exploration_min_items:
            exploration = ranked[-1]
            ranked[-1] = exploration.model_copy(update={"is_exploration": True})
        print(f"[Ranker] 사용자={user_id}, 검증 입력={len(latest_by_url)}건, "
              f"명시 조건 통과={len(ranked)}건, 프로필 버전={profile.version if profile else 1}")
        return {"rank_results": ranked}
    return ranker_node
