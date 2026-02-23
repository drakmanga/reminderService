from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
import html


# ---------- Auth ----------

class LoginRequest(BaseModel):
    username: str
    password: str


# ---------- Reminder ----------

class ReminderCreate(BaseModel):
    message: str = Field(..., max_length=500)
    next_execution: datetime
    recurrence_json: Optional[str] = None

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        return html.escape(v.strip())


class ReminderUpdate(BaseModel):
    message: Optional[str] = Field(None, max_length=500)
    next_execution: Optional[datetime] = None
    recurrence_json: Optional[str] = None
    status: Optional[str] = None

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return html.escape(v.strip())
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        allowed = {"pending", "sent", "completed", "paused", "deleted", "resolved"}
        if v is not None and v not in allowed:
            raise ValueError(f"Status deve essere uno di: {allowed}")
        return v


class ReminderOut(BaseModel):
    id: int
    user_id: int
    message: str
    next_execution: datetime
    recurrence_json: Optional[str]
    status: str
    created_at: datetime
    last_sent_at: Optional[datetime]

