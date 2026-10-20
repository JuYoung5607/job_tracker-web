from datetime import datetime
from pydantic import BaseModel

class AgentError(BaseModel):
    node_name: str
    error_type: str
    message: str
    occurred_at: datetime
    retryable: bool = False
