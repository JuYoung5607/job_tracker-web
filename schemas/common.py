from enum import StrEnum

class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONFLICT = "conflict"
    MISSING = "missing"

class JobStatus(StrEnum):
    CANDIDATE = "candidate"
    UNVERIFIED = "unverified"
    VERIFYING = "verifying"
    RECOMMENDED = "recommended"
    REVIEW = "review"
    REJECTED = "rejected"
    CHANGED = "changed"
    CLOSED = "closed"
    HIDDEN = "hidden"

class SourceStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class DecisionType(StrEnum):
    RECOMMENDED = "recommended"
    REJECTED = "rejected"
    REVIEW = "review"