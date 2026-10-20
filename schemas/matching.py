from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from schemas.screening import ScreeningDecision


class RankResult(BaseModel):
    user_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)

    # 정렬에 사용하는 상대 점수. 취업 가능성이나 확률은 아님.
    score: float = Field(ge=0.0, le=1.0)

    # 사용자에게 보여줄 주요 정렬 이유
    reasons: list[str] = Field(default_factory=list)

    # 취향이 한쪽으로 굳지 않도록 보여주는 탐색용 공고인지
    is_exploration: bool = False

    # 어떤 선호 프로필로 계산했는지 추적
    preference_profile_version: int = Field(ge=1)
    ranker_config_version: str = Field(default="mvp-v1", min_length=1)
    ranked_at: datetime


class FeedItem(BaseModel):
    job_id: str = Field(min_length=1)
    job_version: int = Field(ge=1)
    source_url: HttpUrl
    company_name: str = Field(min_length=1)
    job_title: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    screening_decision: ScreeningDecision
    review_reasons: list[str] = Field(default_factory=list)
    is_exploration: bool = False
    preference_profile_version: int = Field(ge=1)
    ranker_config_version: str = Field(default="mvp-v1", min_length=1)


class FeedResult(BaseModel):
    user_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    generated_at: datetime
    items: list[FeedItem] = Field(default_factory=list)
