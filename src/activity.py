"""Aktivitäts-Log für den Live-Feed und die Recent-Activity-Widgets."""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from .database import get_connection

MAX_ENTRIES = 5_000


def log(kind: str, *, actor: str | None = None, target: str | None = None,
        amount_cents: int | None = None, note: str | None = None) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO activity_log (kind, actor, target, amount_cents, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, actor, target, amount_cents, note),
            )
            conn.execute(
                "DELETE FROM activity_log WHERE id NOT IN "
                "(SELECT id FROM activity_log ORDER BY id DESC LIMIT ?)",
                (MAX_ENTRIES,),
            )
            conn.commit()
    except sqlite3.Error:
        # Aktivitätslog darf niemals kaskadieren – nur best effort.
        pass


def recent(limit: int = 20) -> Iterable[sqlite3.Row]:
    with get_connection() as conn:
        try:
            return conn.execute(
                "SELECT id, timestamp, kind, actor, target, amount_cents, note "
                "FROM activity_log ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
