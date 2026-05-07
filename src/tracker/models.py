"""Domain models for emails, classifications, and applications."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Status = Literal["applied", "interview", "offer", "rejected", "other"]


@dataclass
class Email:
    gmail_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime
    snippet: str
    body_text: str
    internal_date_ms: int | None = None


@dataclass
class Classification:
    is_relevant: bool
    company: str | None
    role: str | None
    location: str | None
    pay: str | None
    status: Status
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class Application:
    company: str
    role: str
    location: str | None
    pay: str | None
    status: Status
    first_seen_at: datetime
    last_updated_at: datetime
    gmail_thread_id: str
