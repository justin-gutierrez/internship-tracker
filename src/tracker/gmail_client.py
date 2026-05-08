"""Gmail API: OAuth, list messages, fetch bodies, profile/history for incremental sync."""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tracker.models import Email

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_credentials(
    credentials_path: Path,
    token_path: Path,
) -> Credentials:
    """Load or refresh OAuth credentials (desktop flow)."""
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Missing {credentials_path}. Download OAuth Desktop client JSON from Google Cloud."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_b64(data: str) -> str:
    pad = 4 - len(data) % 4
    if pad != 4:
        data += "=" * pad
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_body_from_payload(payload: dict | None) -> str:
    """Prefer text/plain; fallback to stripped text/html."""
    if not payload:
        return ""

    plain_chunks: list[str] = []
    html_chunks: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType") or ""
        body = part.get("body") or {}
        data = body.get("data")
        if data:
            decoded = _decode_b64(data)
            if mime == "text/plain":
                plain_chunks.append(decoded)
            elif mime == "text/html":
                html_chunks.append(decoded)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    if plain_chunks:
        return "\n".join(plain_chunks)
    if html_chunks:
        return _strip_html("\n".join(html_chunks))
    # single-part message sometimes has body at root
    root = payload.get("body") or {}
    if root.get("data"):
        return _decode_b64(root["data"])
    return ""


def _header(headers: list[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == name_lower:
            return h.get("value") or ""
    return ""


def parse_message_to_email(msg: dict) -> Email:
    """Parse Gmail API users.messages.get response (format=full)."""
    mid = msg.get("id") or ""
    thread_id = msg.get("threadId") or ""
    payload = msg.get("payload") or {}
    hdrs = payload.get("headers") or []

    sender = _header(hdrs, "From")
    subject = _header(hdrs, "Subject")

    internal_ms = None
    if msg.get("internalDate"):
        try:
            internal_ms = int(msg["internalDate"])
        except (TypeError, ValueError):
            internal_ms = None

    if internal_ms is not None:
        received_at = datetime.fromtimestamp(internal_ms / 1000.0, tz=timezone.utc)
    else:
        received_at = datetime.now(timezone.utc)

    body = _extract_body_from_payload(payload)
    snippet = msg.get("snippet") or ""

    # Cap body size for LLM / DB
    max_body = 50_000
    if len(body) > max_body:
        body = body[:max_body] + "\n...[truncated]"

    return Email(
        gmail_id=mid,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        received_at=received_at,
        snippet=snippet,
        body_text=body,
        internal_date_ms=internal_ms,
    )


def list_message_ids(
    service,
    query: str | None,
    max_emails: int,
    label_ids: list[str] | None = None,
) -> Iterator[str]:
    """Paginate messages.list and yield message ids (newest first)."""
    page_token: str | None = None
    fetched = 0
    user_id = "me"

    while fetched < max_emails:
        remaining = max_emails - fetched
        batch = min(remaining, 500)
        kwargs: dict = {"userId": user_id, "maxResults": batch}
        if query:
            kwargs["q"] = query
        if label_ids:
            kwargs["labelIds"] = label_ids
        if page_token:
            kwargs["pageToken"] = page_token

        resp = service.users().messages().list(**kwargs).execute()
        messages = resp.get("messages") or []
        for m in messages:
            mid = m.get("id")
            if mid:
                yield mid
                fetched += 1
                if fetched >= max_emails:
                    return

        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def get_message_full(service, msg_id: str) -> Email:
    user_id = "me"
    msg = (
        service.users()
        .messages()
        .get(userId=user_id, id=msg_id, format="full")
        .execute()
    )
    return parse_message_to_email(msg)


def get_profile_history_id(service) -> str:
    prof = service.users().getProfile(userId="me").execute()
    hid = prof.get("historyId")
    if not hid:
        raise RuntimeError("Gmail profile missing historyId")
    return str(hid)


def get_profile_email(service) -> str | None:
    """Mailbox address for the authorized user (users.getProfile)."""
    prof = service.users().getProfile(userId="me").execute()
    addr = prof.get("emailAddress")
    return str(addr).strip() if addr else None


def list_history_message_ids(service, start_history_id: str) -> list[str]:
    """Return message ids from history since start_history_id (incremental)."""
    user_id = "me"
    ids: list[str] = []
    page_token: str | None = None

    while True:
        kwargs: dict = {
            "userId": user_id,
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if page_token:
            kwargs["pageToken"] = page_token

        try:
            resp = service.users().history().list(**kwargs).execute()
        except HttpError as e:
            # Invalid history id or too old — caller should fall back to full scan
            logger.warning("history.list failed: %s", e)
            raise

        for h in resp.get("history") or []:
            for added in h.get("messagesAdded") or []:
                m = added.get("message") or {}
                mid = m.get("id")
                if mid:
                    ids.append(mid)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
