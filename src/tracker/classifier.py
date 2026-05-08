"""Orchestrate prefilter -> LLM -> SQLite upserts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlite3 import Connection

from tracker import db as dbmod
from tracker.llm import classify_email
from tracker.models import Email
from tracker.prefilter import prefilter

logger = logging.getLogger(__name__)


def process_email(
    conn: Connection,
    email: Email,
    *,
    ollama_url: str,
    ollama_model: str,
    dry_run: bool,
    skip_if_exists: bool = True,
    source_account: str | None = None,
) -> str:
    """
    Persist one message and optionally classify with Ollama.

    Returns a short status tag: skipped, filtered, dry_run, irrelevant, classified, error.
    """
    if skip_if_exists and dbmod.email_exists(conn, email.gmail_id):
        return "skipped"

    now = datetime.now(timezone.utc)

    if not prefilter(email):
        dbmod.upsert_email(
            conn,
            gmail_id=email.gmail_id,
            thread_id=email.thread_id,
            sender=email.sender,
            subject=email.subject,
            received_at=email.received_at,
            snippet=email.snippet,
            body_text=email.body_text,
            processed_at=now,
            classification=None,
            is_relevant=False,
            company=None,
            role=None,
            location=None,
            pay=None,
            status=None,
            source_account=source_account,
        )
        conn.commit()
        return "filtered"

    if dry_run:
        dbmod.upsert_email(
            conn,
            gmail_id=email.gmail_id,
            thread_id=email.thread_id,
            sender=email.sender,
            subject=email.subject,
            received_at=email.received_at,
            snippet=email.snippet,
            body_text=email.body_text,
            processed_at=now,
            classification={"dry_run": True},
            is_relevant=False,
            company=None,
            role=None,
            location=None,
            pay=None,
            status=None,
            source_account=source_account,
        )
        conn.commit()
        return "dry_run"

    c = classify_email(
        ollama_url,
        ollama_model,
        email.sender,
        email.subject,
        email.received_at.isoformat(),
        email.snippet,
        email.body_text,
    )
    if c.raw_json.get("error"):
        logger.warning("LLM returned error payload for %s", email.gmail_id)

    cls_json = dict(c.raw_json)
    cls_json.update(
        {
            "is_relevant": c.is_relevant,
            "company": c.company,
            "role": c.role,
            "location": c.location,
            "pay": c.pay,
            "status": c.status,
        }
    )

    if not c.is_relevant:
        dbmod.upsert_email(
            conn,
            gmail_id=email.gmail_id,
            thread_id=email.thread_id,
            sender=email.sender,
            subject=email.subject,
            received_at=email.received_at,
            snippet=email.snippet,
            body_text=email.body_text,
            processed_at=now,
            classification=cls_json,
            is_relevant=False,
            company=None,
            role=None,
            location=None,
            pay=None,
            status=c.status,
            source_account=source_account,
        )
        conn.commit()
        return "irrelevant"

    company = c.company or "(unknown company)"
    role = c.role or "(unknown role)"

    dbmod.upsert_email(
        conn,
        gmail_id=email.gmail_id,
        thread_id=email.thread_id,
        sender=email.sender,
        subject=email.subject,
        received_at=email.received_at,
        snippet=email.snippet,
        body_text=email.body_text,
        processed_at=now,
        classification=cls_json,
        is_relevant=True,
        company=company,
        role=role,
        location=c.location,
        pay=c.pay,
        status=c.status,
        source_account=source_account,
    )
    conn.commit()

    dbmod.recompute_application_from_emails(conn, company, role)
    return "classified"
