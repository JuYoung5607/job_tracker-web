from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from schemas.candidate import Evidence
from schemas.common import VerificationStatus


class OverallVerificationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"

class VerificationBudget(BaseModel):
    max_search_calls: int = Field(
        default=3,
        ge=0,
        le=20,
    )
    timeout_seconds: int = Field(
        default=120,
        ge=10,
    )

class FieldVerification(BaseModel):
    field: str = Field(min_length=1)
    extracted_value: Any | None = None
    status: VerificationStatus

    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""

class VerificationResult(BaseModel):
    field_results: list[FieldVerification] = Field(
        default_factory=list
    )
    new_evidence: list[Evidence] = Field(
        default_factory=list
    )

    overall_status: OverallVerificationStatus

    critical_missing_fields: list[str] = Field(
        default_factory=list
    )
    conflicts: list[str] = Field(
        default_factory=list
    )