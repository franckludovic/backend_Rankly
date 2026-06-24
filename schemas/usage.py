from pydantic import BaseModel
from typing import Optional, Literal

class UsageCheckRequest(BaseModel):
    subject_type: Literal["user", "device"]
    subject_id: str
    product: Literal["main_app", "extension"]
    mode: Literal["offline", "online", "n/a"]

class UsageCheckResponse(BaseModel):
    allowed: bool
    remaining: int
    limit: int

class UsageConsumeRequest(BaseModel):
    subject_type: Literal["user", "device"]
    subject_id: str
    product: Literal["main_app", "extension"]
    mode: Literal["offline", "online", "n/a"]
    idempotency_key: str
    audit_id: Optional[str] = None

class UsageConsumeResponse(BaseModel):
    consumed: bool
    already_consumed: bool
    remaining: int
