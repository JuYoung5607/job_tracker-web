import json
from collections import defaultdict
from datetime import datetime, timezone

from nodes.job_store import SQLiteJobStore
from schemas.candidate import JobPosting
from schemas.feedback import FeedbackDecision, FeedbackReasonCategory
from schemas.profile import PreferenceFeature, PreferenceProfile

from .config import settings
from .database import connect


def _values_for_reason(
    job: JobPosting,
    category: FeedbackReasonCategory,
    user_skills: list[str],
) -> list[str]:
    if category == FeedbackReasonCategory.COMPANY:
        return [job.company_name]
    if category == FeedbackReasonCategory.JOB:
        return [job.job_title]
    if category == FeedbackReasonCategory.SKILL:
        text = " ".join([
            job.job_title, *job.requirements, *job.responsibilities, *job.preferences,
        ]).casefold()
        return [skill for skill in user_skills if skill.casefold() in text]
    if category == FeedbackReasonCategory.EXPERIENCE:
        return [str(job.experience_level)] if job.experience_level else []
    if category == FeedbackReasonCategory.LOCATION:
        return job.location
    if category == FeedbackReasonCategory.EMPLOYMENT_TYPE:
        return [job.employment_type] if job.employment_type else []
    return []


def load_preference_profile(user_id: str) -> PreferenceProfile | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM preference_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    return PreferenceProfile.model_validate_json(row["payload"]) if row else None


def rebuild_preference_profile(user_id: str) -> PreferenceProfile:
    """Rebuild from current feedback rows so an updated click is never double-counted."""
    previous = load_preference_profile(user_id)
    from .personalization import load_search_conditions

    conditions = load_search_conditions(user_id)
    user_skills = conditions.skills if conditions else []
    job_store = SQLiteJobStore(settings.database_path)

    with connect() as connection:
        rows = connection.execute(
            "SELECT job_id,decision,reasons_json FROM job_feedback WHERE user_id=?",
            (user_id,),
        ).fetchall()

    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        decision = FeedbackDecision(row["decision"])
        if decision == FeedbackDecision.HOLD:
            continue
        job = job_store.get_by_job_id(row["job_id"])
        if job is None:
            continue
        for raw_category in json.loads(row["reasons_json"]):
            try:
                category = FeedbackReasonCategory(raw_category)
            except ValueError:
                continue
            for value in _values_for_reason(job, category, user_skills):
                normalized = value.strip()
                if not normalized:
                    continue
                bucket = counts[(category.value, normalized)]
                bucket[0 if decision == FeedbackDecision.INTERESTED else 1] += 1

    features = []
    for (category, value), (positive, negative) in sorted(counts.items()):
        observations = positive + negative
        features.append(PreferenceFeature(
            category=category,
            value=value,
            weight=(positive - negative) / observations,
            positive_count=positive,
            negative_count=negative,
            confidence=min(1.0, observations / 3),
        ))

    old_features = [item.model_dump(mode="json") for item in previous.features] if previous else []
    new_features = [item.model_dump(mode="json") for item in features]
    version = previous.version if previous and old_features == new_features else (
        previous.version + 1 if previous else 1
    )
    now = datetime.now(timezone.utc)
    profile = PreferenceProfile(
        user_id=user_id, features=features, version=version, updated_at=now
    )
    with connect() as connection:
        connection.execute(
            "INSERT INTO preference_profiles(user_id,payload,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (user_id, profile.model_dump_json(), now.isoformat()),
        )
    return profile
