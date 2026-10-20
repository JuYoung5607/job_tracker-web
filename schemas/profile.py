from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ProfileChunkType(StrEnum):
    TARGET_JOB = "target_job"
    SKILL = "skill"
    EXCLUSION = "exclusion"
    LOCATION = "location"
    PREFERRED_COMPANY = "preferred_company"
    EXPERIENCE_LEVEL = "experience_level"
    USER_FEEDBACK = "user_feedback"


class ProfileChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    type: ProfileChunkType
    content: str = Field(min_length=1)

    priority: int = Field(
        default=0,
        ge=0,
    )
    version: int = Field(
        default=1,
        ge=1,
    )
    effective_from: datetime


class PreferenceFeature(BaseModel):
    category: str = Field(min_length=1)
    value: str = Field(min_length=1)

    weight: float = Field(default=0.0, ge=-1.0, le=1.0)
    positive_count: int = Field(default=0, ge=0)
    negative_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PreferenceProfile(BaseModel):
    user_id: str = Field(min_length=1)
    features: list[PreferenceFeature] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    updated_at: datetime