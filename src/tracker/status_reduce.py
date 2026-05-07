"""Combine multiple email-level statuses into one application status."""

from __future__ import annotations

from datetime import datetime

from tracker.models import Status

_TERMINAL: frozenset[str] = frozenset({"offer", "rejected"})
_RANK: dict[str, int] = {"interview": 3, "applied": 2, "other": 1}


def reduce_status(classifications: list[tuple[datetime, str]]) -> Status:
    """
    Reduce (received_at, status) pairs to a single Status.

    - If any terminal (offer/rejected) exists, use the **most recent** terminal.
    - Else use the highest rank among interview > applied > other.
    """
    if not classifications:
        return "other"

    terminals = [(dt, st) for dt, st in classifications if st in _TERMINAL]
    if terminals:
        _, st = max(terminals, key=lambda x: x[0])
        return st  # type: ignore[return-value]

    best: Status = "other"
    best_rank = 0
    for _, st in classifications:
        r = _RANK.get(st, 0)
        if r > best_rank:
            best_rank = r
            best = st  # type: ignore[assignment]
    return best
