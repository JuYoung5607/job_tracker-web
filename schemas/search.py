from enum import StrEnum

from pydantic import BaseModel, Field

class ExperienceLevel(StrEnum):
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"

class SearchConditions(BaseModel):
    target_jobs: list[str] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    exclusion_conditions: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    experience_levels: list[ExperienceLevel] = Field(default_factory=list, description= "빈 리스트이면 경력 수준 제한 없음")
    max_experience_years: int | None = Field(default=None, ge=0)
    employment_types: list[str] = Field(default_factory=list)
    preferred_source_domains: list[str] = Field(default_factory=list)
    
class SearchBudget(BaseModel):
    max_search_calls: int = Field(default=10, ge=1, le= 100) # ge: greater than or equal, le: less than or equal
    max_candidates: int = Field(default=30, ge=1, le=500)
    timeout_seconds: int = Field(default=300, ge=30)

class SearchQuery(BaseModel):
    query: str = Field(min_length=1)
    search_track: str = Field(min_length=1)
    allowed_domains: list[str] = Field(default_factory=list)

class SearchPlan(BaseModel):
    search_tracks: list[str] = Field(min_length=1)
    queries: list[SearchQuery] = Field(min_length=1)
    target_companies: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
