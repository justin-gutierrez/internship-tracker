"""SQLite persistence and application row aggregation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from tracker.models import Application, Status
from tracker.status_reduce import reduce_status

META_HISTORY_KEY = "gmail_history_id"  # legacy single-mailbox key (migrated away on write)


def history_meta_key_for_email(profile_email: str) -> str:
    return f"gmail_history_id:{profile_email.strip().lower()}"


def has_per_mailbox_history_keys(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM meta WHERE key LIKE ? LIMIT 1",
        ("gmail_history_id:%",),
    ).fetchone()
    return row is not None


def get_gmail_history_id(conn: sqlite3.Connection, profile_email: str) -> str | None:
    """History cursor for this mailbox; falls back to legacy key only if no per-mailbox rows yet."""
    pe = profile_email.strip().lower()
    if not pe:
        return None
    v = get_meta(conn, history_meta_key_for_email(pe))
    if v:
        return v
    if has_per_mailbox_history_keys(conn):
        return None
    return get_meta(conn, META_HISTORY_KEY)


def set_gmail_history_id(conn: sqlite3.Connection, profile_email: str, history_id: str) -> None:
    """Save history id for mailbox and drop legacy key so a second inbox never reuses it."""
    pe = profile_email.strip().lower()
    if not pe:
        raise ValueError("profile_email required for history")
    set_meta(conn, history_meta_key_for_email(pe), history_id)
    conn.execute("DELETE FROM meta WHERE key = ?", (META_HISTORY_KEY,))
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS emails (
            gmail_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            sender TEXT,
            subject TEXT,
            received_at TEXT NOT NULL,
            snippet TEXT,
            body_text TEXT,
            processed_at TEXT NOT NULL,
            classification_json TEXT,
            is_relevant INTEGER NOT NULL DEFAULT 0,
            company TEXT,
            role TEXT,
            location TEXT,
            pay TEXT,
            status TEXT,
            source_account TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(thread_id);
        CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at);

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_key TEXT NOT NULL,
            role_key TEXT NOT NULL,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            location TEXT,
            pay TEXT,
            status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_updated_at TEXT NOT NULL,
            gmail_thread_id TEXT NOT NULL,
            UNIQUE(company_key, role_key)
        );

        CREATE INDEX IF NOT EXISTS idx_apps_keys ON applications(company_key, role_key);
        """
    )
    conn.commit()
    _migrate_emails_source_account(conn)


def _migrate_emails_source_account(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(emails)").fetchall()
    cols = {r[1] for r in rows}
    if cols and "source_account" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN source_account TEXT")
        conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def email_exists(conn: sqlite3.Connection, gmail_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM emails WHERE gmail_id = ?", (gmail_id,)).fetchone()
    return row is not None


def upsert_email(
    conn: sqlite3.Connection,
    *,
    gmail_id: str,
    thread_id: str,
    sender: str,
    subject: str,
    received_at: datetime,
    snippet: str,
    body_text: str,
    processed_at: datetime,
    classification: dict[str, Any] | None,
    is_relevant: bool,
    company: str | None,
    role: str | None,
    location: str | None,
    pay: str | None,
    status: str | None,
    source_account: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO emails (
            gmail_id, thread_id, sender, subject, received_at, snippet, body_text,
            processed_at, classification_json, is_relevant, company, role, location, pay, status,
            source_account
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gmail_id) DO UPDATE SET
            thread_id = excluded.thread_id,
            sender = excluded.sender,
            subject = excluded.subject,
            received_at = excluded.received_at,
            snippet = excluded.snippet,
            body_text = excluded.body_text,
            processed_at = excluded.processed_at,
            classification_json = excluded.classification_json,
            is_relevant = excluded.is_relevant,
            company = excluded.company,
            role = excluded.role,
            location = excluded.location,
            pay = excluded.pay,
            status = excluded.status,
            source_account = excluded.source_account
        """,
        (
            gmail_id,
            thread_id,
            sender,
            subject,
            received_at.isoformat(),
            snippet,
            body_text,
            processed_at.isoformat(),
            json.dumps(classification) if classification is not None else None,
            1 if is_relevant else 0,
            company,
            role,
            location,
            pay,
            status,
            source_account,
        ),
    )


def _keys(company: str, role: str) -> tuple[str, str]:
    return company.strip().lower(), role.strip().lower()


def recompute_application_from_emails(
    conn: sqlite3.Connection,
    company: str,
    role: str,
) -> None:
    """Rebuild one applications row from all relevant emails with same company/role keys."""
    ck, rk = _keys(company, role)
    rows = conn.execute(
        """
        SELECT received_at, status, thread_id, location, pay, company, role
        FROM emails
        WHERE is_relevant = 1 AND lower(company) = ? AND lower(role) = ?
        """,
        (ck, rk),
    ).fetchall()

    if not rows:
        conn.execute(
            "DELETE FROM applications WHERE company_key = ? AND role_key = ?",
            (ck, rk),
        )
        conn.commit()
        return

    pairs: list[tuple[datetime, str]] = []
    loc: str | None = None
    pay: str | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    display_company = company
    display_role = role
    thread_id = ""

    for r in rows:
        ra = datetime.fromisoformat(r["received_at"])
        st = r["status"] or "other"
        pairs.append((ra, st))
        if r["location"]:
            loc = r["location"]
        if r["pay"]:
            pay = r["pay"]
        if first_ts is None or ra < first_ts:
            first_ts = ra
        if last_ts is None or ra > last_ts:
            last_ts = ra
            thread_id = r["thread_id"] or ""
            display_company = r["company"] or display_company
            display_role = r["role"] or display_role

    if not thread_id and rows:
        thread_id = rows[0]["thread_id"] or ""

    final_status: Status = reduce_status(pairs)
    assert first_ts and last_ts

    conn.execute(
        """
        INSERT INTO applications (
            company_key, role_key, company, role, location, pay, status,
            first_seen_at, last_updated_at, gmail_thread_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_key, role_key) DO UPDATE SET
            company = excluded.company,
            role = excluded.role,
            location = excluded.location,
            pay = excluded.pay,
            status = excluded.status,
            first_seen_at = excluded.first_seen_at,
            last_updated_at = excluded.last_updated_at,
            gmail_thread_id = excluded.gmail_thread_id
        """,
        (
            ck,
            rk,
            display_company,
            display_role,
            loc,
            pay,
            final_status,
            first_ts.isoformat(),
            last_ts.isoformat(),
            thread_id,
        ),
    )
    conn.commit()


def list_applications(conn: sqlite3.Connection) -> list[Application]:
    rows = conn.execute(
        """
        SELECT company, role, location, pay, status, first_seen_at, last_updated_at, gmail_thread_id
        FROM applications
        ORDER BY last_updated_at DESC
        """
    ).fetchall()
    out: list[Application] = []
    for r in rows:
        out.append(
            Application(
                company=r["company"],
                role=r["role"],
                location=r["location"],
                pay=r["pay"],
                status=r["status"],  # type: ignore[arg-type]
                first_seen_at=datetime.fromisoformat(r["first_seen_at"]),
                last_updated_at=datetime.fromisoformat(r["last_updated_at"]),
                gmail_thread_id=r["gmail_thread_id"],
            )
        )
    return out


def touch_applications_for_thread(conn: sqlite3.Connection, thread_id: str) -> None:
    """Recompute application rows for every distinct (company, role) in this thread."""
    rows = conn.execute(
        """
        SELECT DISTINCT company, role FROM emails
        WHERE thread_id = ? AND is_relevant = 1 AND company IS NOT NULL AND role IS NOT NULL
        """,
        (thread_id,),
    ).fetchall()
    for r in rows:
        recompute_application_from_emails(conn, r["company"], r["role"])


def touch_application_keys(conn: sqlite3.Connection, company: str, role: str) -> None:
    recompute_application_from_emails(conn, company, role)
