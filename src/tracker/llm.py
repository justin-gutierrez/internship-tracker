"""Ollama HTTP client: JSON-mode classification."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from tracker.models import Classification, Status

logger = logging.getLogger(__name__)

ALLOWED_STATUS: frozenset[str] = frozenset(
    {"applied", "interview", "offer", "rejected", "other"}
)


SYSTEM_PROMPT = """You classify recruiting emails about internships / new grad roles.
Return ONLY valid JSON with keys:
  is_relevant (boolean): true if this email is about a specific job application, internship, co-op, or interview process.
  company (string or null): employer name if identifiable.
  role (string or null): job title e.g. "Software Engineering Intern".
  location (string or null): city/region/remote if stated.
  pay (string or null): compensation if explicitly mentioned; else null.
  status (string): one of applied | interview | offer | rejected | other
    - applied: confirmation of application receipt, "thank you for applying"
    - interview: scheduling, invitation to interview, next steps for interviewing
    - offer: job offer or intent to offer
    - rejection: not moving forward, position filled for your candidacy, regret
    - other: recruiting newsletter or unrelated

Use null for unknown strings. Be conservative: if unsure of company/role, set null."""

USER_TEMPLATE = """From: {sender}
Subject: {subject}
Date (UTC): {received_at}

Snippet:
{snippet}

Body (may be truncated):
{body}
"""


def _normalize_status(raw: str | None) -> Status:
    if not raw:
        return "other"
    s = raw.strip().lower()
    if s in ALLOWED_STATUS:
        return s  # type: ignore[return-value]
    return "other"


def classify_email(
    ollama_url: str,
    model: str,
    sender: str,
    subject: str,
    received_at_iso: str,
    snippet: str,
    body: str,
    retry: bool = True,
) -> Classification:
    """Call Ollama chat API with JSON format; retry once with stricter truncation."""
    url = ollama_url.rstrip("/") + "/api/chat"

    attempts: list[tuple[int, float]] = [(12000, 0.1)]
    if retry:
        attempts.append((6000, 0.0))

    last_error: str | None = None
    for body_limit, temp in attempts:
        user_content = USER_TEMPLATE.format(
            sender=sender,
            subject=subject,
            received_at=received_at_iso,
            snippet=snippet[:2000],
            body=body[:body_limit],
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {"temperature": temp},
        }
        try:
            r = requests.post(url, json=payload, timeout=120)
            r.raise_for_status()
            return parse_response(r.json())
        except Exception as e:
            last_error = str(e)
            logger.warning("Ollama attempt failed (limit=%s): %s", body_limit, e)

    return Classification(
        is_relevant=False,
        company=None,
        role=None,
        location=None,
        pay=None,
        status="other",
        raw_json={"error": last_error or "unknown"},
    )


def parse_response(resp_json: dict[str, Any]) -> Classification:
    msg = (resp_json.get("message") or {}).get("content") or ""
    data = _extract_json(msg)
    return _dict_to_classification(data, raw_fallback=msg)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _dict_to_classification(data: dict[str, Any], raw_fallback: str = "") -> Classification:
    is_rel = bool(data.get("is_relevant", False))
    company = data.get("company")
    role = data.get("role")
    location = data.get("location")
    pay = data.get("pay")
    if company is not None and not isinstance(company, str):
        company = str(company)
    if role is not None and not isinstance(role, str):
        role = str(role)
    if location is not None and not isinstance(location, str):
        location = str(location)
    if pay is not None and not isinstance(pay, str):
        pay = str(pay)

    st = _normalize_status(data.get("status") if isinstance(data.get("status"), str) else None)
    raw = dict(data)
    if raw_fallback and not raw:
        raw = {"raw": raw_fallback[:500]}
    return Classification(
        is_relevant=is_rel,
        company=company.strip() if isinstance(company, str) and company.strip() else None,
        role=role.strip() if isinstance(role, str) and role.strip() else None,
        location=location.strip() if isinstance(location, str) and location.strip() else None,
        pay=pay.strip() if isinstance(pay, str) and pay.strip() else None,
        status=st,
        raw_json=raw,
    )
