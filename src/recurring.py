"""Wiederkehrende Buchungen (Miete, Wartung, Beiträge …).

Vorlage → beim Fälligkeitsdatum wird automatisch (falls ``auto_post``) oder
manuell eine Buchung im Kassenbuch erzeugt und das nächste Fälligkeitsdatum
fortgeschrieben.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from . import ledger
from .database import get_connection

_LOG = logging.getLogger(__name__)

INTERVALS = {"daily", "weekly", "monthly", "yearly", "manual"}


@dataclass
class Recurring:
    id: int
    name: str
    amount_cents: int
    from_account_id: Optional[int]
    to_account_id: Optional[int]
    kind: str
    interval: str
    next_due: Optional[str]
    last_posted: Optional[str]
    active: int
    auto_post: int
    note: Optional[str]


def _row(r) -> Recurring:
    return Recurring(**{k: r[k] for k in r.keys() if k in Recurring.__dataclass_fields__})


def list_all() -> list[Recurring]:
    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM recurring_expenses ORDER BY next_due, name"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [_row(r) for r in rows]


def get(rec_id: int) -> Optional[Recurring]:
    with get_connection() as conn:
        r = conn.execute(
            "SELECT * FROM recurring_expenses WHERE id=?", (int(rec_id),)
        ).fetchone()
    return _row(r) if r else None


def create(name: str, amount_cents: int, *,
           from_account_id: int | None, to_account_id: int | None,
           kind: str = "expense", interval: str = "monthly",
           next_due: str | None = None, auto_post: bool = True,
           note: str | None = None) -> int:
    if interval not in INTERVALS:
        raise ValueError("ungültiges Intervall")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO recurring_expenses "
            "(name, amount_cents, from_account_id, to_account_id, kind, interval, "
            " next_due, active, auto_post, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (name.strip(), int(amount_cents), from_account_id, to_account_id,
             kind, interval, next_due, 1 if auto_post else 0, note),
        )
        conn.commit()
        return int(cur.lastrowid)


def update(rec_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE recurring_expenses SET {cols} WHERE id=?",
            (*fields.values(), int(rec_id)),
        )
        conn.commit()


def delete(rec_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM recurring_expenses WHERE id=?", (int(rec_id),))
        conn.commit()


def _next(interval: str, today: date) -> date:
    if interval == "daily":
        return today + timedelta(days=1)
    if interval == "weekly":
        return today + timedelta(days=7)
    if interval == "yearly":
        return today.replace(year=today.year + 1)
    if interval == "monthly":
        y = today.year + (1 if today.month == 12 else 0)
        m = 1 if today.month == 12 else today.month + 1
        d = min(today.day, 28)
        return date(y, m, d)
    return today  # manual – nicht auto-fortschreiben


def post_once(rec_id: int, actor: str | None = None) -> Optional[int]:
    """Bucht die Vorlage einmal (Buchung + next_due-Update). Liefert Ledger-ID."""
    rec = get(rec_id)
    if not rec or not rec.active:
        return None
    entry_id = ledger.post(
        rec.kind, rec.amount_cents,
        from_account=rec.from_account_id, to_account=rec.to_account_id,
        ref=f"recurring #{rec.id}", actor=actor,
        note=rec.note or f"Wiederkehrend: {rec.name}",
    )
    today = date.today()
    nxt = _next(rec.interval, today) if rec.interval != "manual" else rec.next_due
    with get_connection() as conn:
        conn.execute(
            "UPDATE recurring_expenses SET last_posted=CURRENT_TIMESTAMP, next_due=? WHERE id=?",
            (nxt.isoformat() if isinstance(nxt, date) else nxt, int(rec_id)),
        )
        conn.commit()
    return entry_id


def run_due(now: Optional[datetime] = None, actor: str = "scheduler") -> list[int]:
    """Alle fälligen automatischen Vorlagen buchen. Rückgabe: Liste angewandter IDs."""
    now = now or datetime.now()
    today_iso = now.date().isoformat()
    applied: list[int] = []
    for rec in list_all():
        if not rec.active or not rec.auto_post:
            continue
        if not rec.next_due or rec.next_due > today_iso:
            continue
        try:
            post_once(rec.id, actor=actor)
            applied.append(rec.id)
        except Exception as e:  # pragma: no cover
            _LOG.warning("recurring #%s failed: %s", rec.id, e)
    return applied
