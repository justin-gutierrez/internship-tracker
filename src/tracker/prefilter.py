"""Cheap keyword + ATS sender heuristics before calling the LLM."""

from __future__ import annotations

import re

from tracker.models import Email

# Common recruiting / ATS domains (substring match on From)
ATS_DOMAIN_HINTS = (
    "@greenhouse.io",
    "@lever.co",
    "@myworkday.com",
    "@smartrecruiters.com",
    "@ashbyhq.com",
    "@jobs.lever.co",
    "@notifications.smartrecruiters.com",
    "@icims.com",
    "@taleo.net",
    "@oraclecloud.com",
    "@jobvite.com",
    "@bamboohr.com",
    "@recruitee.com",
    "@joinhandshake.com",
    "@email.joinhandshake.com",
    "@mail.joinhandshake.com",
)

# Keywords in subject/snippet/body (case-insensitive)
KEYWORD_RE = re.compile(
    r"\b(intern|internship|internships|co-?op|coop|application submitted|"
    r"application received|thank you for applying|we received your application|"
    r"update on your application|status of your application|next steps|interview|"
    r"phone screen|scheduling|offer|congratulations|unfortunately|regret to inform|"
    r"not moving forward|not selected|position has been filled|hiring team|"
    r"recruiting|talent acquisition|handshake)\b",
    re.I,
)


def prefilter(email: Email) -> bool:
    """
    Return True if the email might be internship/recruiting-related.
    False positives are OK; false negatives cost missed rows.
    """
    sender_l = email.sender.lower()
    for hint in ATS_DOMAIN_HINTS:
        if hint.strip() and hint.strip() in sender_l:
            return True

    blob = f"{email.subject}\n{email.snippet}\n{email.body_text[:8000]}"
    if KEYWORD_RE.search(blob):
        return True

    return False
