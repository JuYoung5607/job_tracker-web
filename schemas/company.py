from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl

class CompanyCategory(StrEnum):
    LARGE_ENTERPRISE = "large_enterprise"
    MID_SIZED = "mid_sized"
    SMALL_BUSINESS = "small_business"
    PUBLIC_INSTITUTION = "public_institution"
    RESEARCH_INSTITUTE = "research_institute"
    STARTUP = "startup"
    OTHER = "other"
    UNKNOWN = "unknown"

class CompanyVerificationStatus(StrEnum):
    VERIFIED = "verified"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"

class CompanyEvidence(BaseModel):
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_url: HttpUrl
    source_name: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    retrieved_at: datetime

    verification_status: CompanyVerificationStatus = (
        CompanyVerificationStatus.UNKNOWN
    )

class CompanyProfile(BaseModel):
    company_name: str = Field(min_length=1)

    company_category: CompanyCategory = CompanyCategory.UNKNOWN
    industry: str = "unknown"
    company_size: str = "unknown"
    headquarters: str = "unknown"

    official_company_url: HttpUrl | None = None

    company_evidence: list[CompanyEvidence] = Field(
        default_factory=list
    )

