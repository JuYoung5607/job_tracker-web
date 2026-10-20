from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    CHANGED = "changed"
    CLOSED = "closed"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class LifecycleResult(BaseModel):
    previous_status: LifecycleStatus | None = None
    next_status: LifecycleStatus

    transition_reason: str = Field(min_length=1)
    changed_fields: list[str] = Field(default_factory=list)
    review_fields: list[str] = Field(default_factory=list)

    should_publish: bool = False
    should_preserve_existing: bool = False


class PublishStatus(StrEnum):
    PUBLISHED = "published"
    BLOCKED = "blocked"
    UNCHANGED = "unchanged"


class ChangeSummary(BaseModel):
    created_count: int = Field(default=0, ge=0)
    updated_count: int = Field(default=0, ge=0)
    closed_count: int = Field(default=0, ge=0)
    unverified_count: int = Field(default=0, ge=0)


class PublishResult(BaseModel):
    publish_status: PublishStatus

    result_version: int = Field(ge=1)
    generated_at: datetime

    change_summary: ChangeSummary = Field(
        default_factory=ChangeSummary
    )
    published_job_ids: list[str] = Field(
        default_factory=list
    )
    blocked_reasons: list[str] = Field(
        default_factory=list
    )
