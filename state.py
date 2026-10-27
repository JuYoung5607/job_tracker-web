from typing import TypedDict, Annotated, Required, NotRequired
from datetime import datetime

from schemas.candidate import (
    Candidate,
    Evidence,
    JobDocument,
    JobPosting,
    JobPostingDraft,
)
from schemas.company import CompanyProfile
from schemas.errors import AgentError
from schemas.lifecycle import LifecycleResult
from schemas.search import SearchBudget, SearchConditions
from schemas.screening import ExtractedPostingFacts, ScreeningResult
from schemas.verification import VerificationResult

import operator

class State(TypedDict):
    # RUN
    run_id: Required[str]
    user_id: Required[str]
    started_at: Required[datetime]

    #Search
    search_conditions: Required[SearchConditions]
    last_searched_at: NotRequired[datetime | None]
    search_budget: Required[SearchBudget]
    candidates: NotRequired[list[Candidate]]
    candidate_cursor: NotRequired[int]

    #Iterating
    discovered_candidates: NotRequired[list[Candidate]]
    candidate_processing_complete: NotRequired[bool]

    #Candidate_Processing
    current_candidate: NotRequired[Candidate | None]
    job_document: NotRequired[JobDocument | None]
    extracted_facts: NotRequired[ExtractedPostingFacts | None]
    job_posting_draft: NotRequired[JobPostingDraft | None]
    evidence: NotRequired[list[Evidence]]
    company_profile: NotRequired[CompanyProfile | None]
    verification_result: NotRequired[VerificationResult | None]
    screening_result: NotRequired[ScreeningResult | None]

    #Lifecycle/Publish
    existing_job: NotRequired[JobPosting | None]
    lifecycle_result: NotRequired[LifecycleResult | None]
    persisted_job_id: NotRequired[str | None]



    #Control
    errors: NotRequired[Annotated[list[AgentError], operator.add]]
    retry_counts: NotRequired[dict[str, int]]


