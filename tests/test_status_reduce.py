"""Tests for status aggregation."""

from datetime import datetime, timezone

from tracker.status_reduce import reduce_status


def ts(day: int) -> datetime:
    return datetime(2025, 1, day, tzinfo=timezone.utc)


def test_terminal_most_recent_wins():
    assert (
        reduce_status(
            [
                (ts(1), "applied"),
                (ts(5), "interview"),
                (ts(10), "rejected"),
            ]
        )
        == "rejected"
    )


def test_offer_beats_older_rejection():
    assert (
        reduce_status(
            [
                (ts(1), "rejected"),
                (ts(3), "offer"),
            ]
        )
        == "offer"
    )


def test_rejection_beats_older_offer():
    assert (
        reduce_status(
            [
                (ts(1), "offer"),
                (ts(4), "rejected"),
            ]
        )
        == "rejected"
    )


def test_no_terminal_prefers_interview_over_applied():
    assert (
        reduce_status(
            [
                (ts(1), "applied"),
                (ts(2), "applied"),
                (ts(3), "interview"),
            ]
        )
        == "interview"
    )


def test_empty_defaults_other():
    assert reduce_status([]) == "other"
