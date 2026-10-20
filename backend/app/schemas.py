from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("올바른 이메일 주소를 입력해 주세요.")
        return normalized


class UserView(BaseModel):
    user_id: str
    email: str


class SearchConditionsInput(BaseModel):
    target_jobs: list[str] = Field(min_length=1, max_length=10)
    skills: list[str] = Field(default_factory=list, max_length=30)
    exclusion_conditions: list[str] = Field(default_factory=list, max_length=20)
    locations: list[str] = Field(default_factory=list, max_length=20)
    experience_levels: list[Literal["entry", "junior", "mid", "senior"]] = Field(
        default_factory=list
    )
    max_experience_years: int | None = Field(default=None, ge=0, le=50)
    employment_types: list[str] = Field(default_factory=list, max_length=10)
    preferred_source_domains: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("target_jobs", "skills", "exclusion_conditions", "locations",
                     "employment_types", "preferred_source_domains")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))


class FeedbackInput(BaseModel):
    decision: Literal["interested", "hold", "not_interested"]
    reasons: list[str] = Field(default_factory=list, max_length=8)
    comment: str | None = Field(default=None, max_length=500)


class CorrectionRequestInput(BaseModel):
    job_id: str | None = None
    source_url: HttpUrl
    request_type: Literal["correction", "removal"]
    message: str = Field(min_length=5, max_length=2000)
