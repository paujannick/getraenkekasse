"""Audit-Log für Admin-Aktionen (Web + API).

Die Einträge landen in der Tabelle ``audit_log`` und werden bei jedem
Import per :func:`ensure_schema` idempotent angelegt.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from .database import get_connection

LOCAL_TZ = ZoneInfo("Europe/Berlin")
MAX_ENTRIES = 20_000


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS audit_log ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "timestamp DATETIME NOT NULL, "
    "actor TEXT, "
    "ip TEXT, "
    "action TEXT NOT NULL, "
    "target TEXT, "
    "details TEXT"
    ")"
)


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    own = False
    if conn is None:
        conn = get_connection()
        own = True
    conn.execute(_SCHEMA)
    conn.commit()
    if own:
        conn.close()


def log(
    action: str,
    *,
    actor: str | None = None,
    ip: str | None = None,
    target: str | None = None,
    details: str | None = None,
) -> None:
    """Schreibe einen Audit-Eintrag. Fehler werden geschluckt (nie kaskadieren)."""
    try:
        now = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with get_connection() as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO audit_log (timestamp, actor, ip, action, target, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, actor, ip, action, target, details),
            )
            conn.execute(
                "DELETE FROM audit_log WHERE id NOT IN ("
                "SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
                (MAX_ENTRIES,),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def fetch(limit: int = 200) -> Iterable[sqlite3.Row]:
    with get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "SELECT id, timestamp, actor, ip, action, target, details "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        return cur.fetchall()
