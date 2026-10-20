from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ExtractedClaim(BaseModel):
    """원문에서 확인한 값과 그 값을 뒷받침하는 짧은 인용."""

    model_config = ConfigDict(extra="forbid")
    value: str | None
    quote: str | None


class RoleRelation(StrEnum):
    RELATED = "related"
    UNRELATED = "unrelated"
    UNCERTAIN = "uncertain"


class ExtractedPosition(BaseModel):
    """복수 직무 공고에서 한 포지션의 직무와 필수 경력을 묶습니다."""

    model_config = ConfigDict(extra="forbid")
    job_title: ExtractedClaim
    experience_requirement: ExtractedClaim
    role_relation: RoleRelation
    role_evidence: str | None
    scope_quote: str | None = None


class ExtractedPostingFacts(BaseModel):
    """공고 본문에서 추출한 사실. 모르면 두 필드 모두 null입니다."""

    model_config = ConfigDict(extra="forbid")
    company_name: ExtractedClaim
    job_title: ExtractedClaim
    experience_requirement: ExtractedClaim
    deadline: ExtractedClaim
    recruiting_status: ExtractedClaim
    employment_type: ExtractedClaim
    location: ExtractedClaim
    role_relation: RoleRelation
    role_evidence: str | None
    positions: list[ExtractedPosition] = Field(default_factory=list)
    is_multi_position: bool = False
    common_experience_requirement: ExtractedClaim | None = None
    common_experience_scope_quote: str | None = None


class ScreeningDecision(StrEnum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    REVIEW = "review"


class ScreeningResult(BaseModel):
    candidate_id: str
    source_url: HttpUrl
    decision: ScreeningDecision
    reasons: list[str] = Field(default_factory=list)
    closing_date: date | None = None
    minimum_experience_years: int | None = None
    experience_basis: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
