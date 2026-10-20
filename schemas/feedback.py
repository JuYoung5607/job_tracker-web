from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FeedbackDecision(StrEnum):
    INTERESTED = "interested"
    HOLD = "hold"
    NOT_INTERESTED = "not_interested"


class FeedbackReasonCategory(StrEnum):
    COMPANY = "company"
    JOB = "job"
    SKILL = "skill"
    EXPERIENCE = "experience"
    LOCATION = "location"
    EMPLOYMENT_TYPE = "employment_type"
    OTHER = "other"


class FeedbackReasonDetail(BaseModel):
    category: FeedbackReasonCategory
    value: str | None = None


class JobFeedback(BaseModel):
    feedback_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    decision: FeedbackDecision
    reasons: list[FeedbackReasonDetail] = Field(default_factory=list)
    comment: str | None = None
    created_at: datetime
    idempotency_key: str | None = None