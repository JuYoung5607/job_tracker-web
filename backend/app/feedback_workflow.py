"""Optional feedback execution, separate from feed personalization."""

import json
from dataclasses import dataclass
from uuid import uuid4

from .database import connect
from .personalization import refresh_user_feed
from .preference_updater import rebuild_preference_profile
from .schemas import FeedbackInput


@dataclass(frozen=True)
class FeedbackRunState:
    user_id: str
    job_id: str
    decision: str
    saved_at: str
    preference_profile_version: int


def record_feedback(user_id: str, job_id: str, value: FeedbackInput, *, saved_at: str) -> FeedbackRunState:
    """Save a user's current choice and update their persistent preference profile."""
    with connect() as connection:
        connection.execute(
            "INSERT INTO job_feedback(feedback_id,user_id,job_id,decision,reasons_json,comment,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,job_id) DO UPDATE SET "
            "decision=excluded.decision,reasons_json=excluded.reasons_json,comment=excluded.comment,"
            "updated_at=excluded.updated_at",
            (str(uuid4()), user_id, job_id, value.decision,
             json.dumps(value.reasons, ensure_ascii=False), value.comment, saved_at, saved_at),
        )
    profile = rebuild_preference_profile(user_id)
    return FeedbackRunState(user_id, job_id, value.decision, saved_at, profile.version)


def personalize_after_feedback(state: FeedbackRunState) -> int:
    """Invoke the same feed calculation also used when no feedback exists."""
    return refresh_user_feed(state.user_id)
