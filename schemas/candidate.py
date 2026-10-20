from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl

from schemas.common import VerificationStatus, JobStatus, SourceStatus
from schemas.lifecycle import LifecycleStatus

class SourceType(StrEnum):
    OFFICIAL = "official"
    JOB_PLATFORM = "job_platform"
    SEARCH_ENGINE = "search_engine"
    OTHER= "other"

class CandidatePageType(StrEnum):
    JOB_DETAIL = "job_detail"
    LISTING = "listing"
    UNKNOWN = "unknown"

class AccessStatus(StrEnum):
    ACCESSIBLE = "accessible"
    BLOCKED = "blocked"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    UNAVAILABLE = "unavailable"

class Candidate(BaseModel):
    candidate_id: str 
    url: HttpUrl
    page_type: CandidatePageType = CandidatePageType.UNKNOWN
    source_type: SourceType
    source_name: str

    discovery_source: str
    discovered_at: datetime

    search_query: str | None= None
    search_track: str | None = None
    title_hint: str | None = None
    company_hint: str | None = None

    parent_candidate_id: str | None = None
    discovery_depth: int = 0

class Evidence(BaseModel):
    evidence_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    source_url: HttpUrl
    source_section: str = Field(min_length=1)
    verification_status: VerificationStatus = (VerificationStatus.UNVERIFIED)
    verified_at: datetime | None = None

class JobDocument(BaseModel):
    document_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    source_url: HttpUrl
    page_title: str
    content: str = Field(min_length=1)
    fetched_at: datetime

    http_status: int | None = Field(
        default=None,
        ge=100,
        le=599,
    )
    is_official: bool = False
    access_status: AccessStatus
    active_application_evidence: str | None = None

class JobPostingDraft(BaseModel):
    company_name: str = Field(min_length=1)
    job_title: str = Field(min_length=1)

    employment_type: str | None = None
    experience_level: str | None = None

    education: list[str] = Field(
        default_factory=list
    )
    location: list[str] = Field(
        default_factory=list
    )

    opened_at: datetime | None = None
    closes_at: datetime | None = None

    responsibilities: list[str] = Field(
        default_factory=list
    )
    requirements: list[str] = Field(
        default_factory=list
    )
    preferences: list[str] = Field(
        default_factory=list
    )
    source_url: HttpUrl

class JobPosting(JobPostingDraft):
    job_id: str = Field(min_length=1)

    status: JobStatus = JobStatus.CANDIDATE
    lifecycle_status: LifecycleStatus = LifecycleStatus.UNKNOWN

    source_urls: list[HttpUrl] = Field(
        default_factory=list
    )
    source_status: SourceStatus = SourceStatus.UNKNOWN

    evidence: list[Evidence] = Field(
        default_factory=list
    )

    content_hash: str = Field(min_length=1)

    first_seen_at: datetime
    last_seen_at: datetime

    result_version: int = Field(
        default=1,
        ge=1,
    )

    created_at: datetime
    updated_at: datetime

