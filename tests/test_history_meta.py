"""Per-mailbox Gmail history id meta keys."""

from __future__ import annotations

from pathlib import Path

from tracker import db as dbmod


def test_legacy_fallback_until_per_mailbox_exists(tmp_path: Path) -> None:
    dbp = tmp_path / "t.db"
    conn = dbmod.connect(dbp)
    dbmod.init_schema(conn)
    dbmod.set_meta(conn, dbmod.META_HISTORY_KEY, "legacy123")

    assert dbmod.get_gmail_history_id(conn, "You@Example.com") == "legacy123"
    assert dbmod.get_gmail_history_id(conn, "other@x.com") == "legacy123"


def test_per_mailbox_keys_no_cross_fallback(tmp_path: Path) -> None:
    dbp = tmp_path / "t.db"
    conn = dbmod.connect(dbp)
    dbmod.init_schema(conn)
    dbmod.set_meta(conn, dbmod.META_HISTORY_KEY, "legacy123")
    dbmod.set_gmail_history_id(conn, "you@example.com", "111")

    assert dbmod.get_gmail_history_id(conn, "you@example.com") == "111"
    assert dbmod.get_meta(conn, dbmod.META_HISTORY_KEY) is None
    assert dbmod.get_gmail_history_id(conn, "other@x.com") is None


def test_set_gmail_history_id_normalizes_email(tmp_path: Path) -> None:
    dbp = tmp_path / "t.db"
    conn = dbmod.connect(dbp)
    dbmod.init_schema(conn)
    dbmod.set_gmail_history_id(conn, "  Me@School.EDU  ", "999")

    assert dbmod.get_gmail_history_id(conn, "me@school.edu") == "999"
