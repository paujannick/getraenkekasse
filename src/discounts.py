"""Rabatt-/Happy-Hour-Logik.

Rabatte werden in der Tabelle ``discounts`` gepflegt und wirken auf den
Verkaufspreis eines Getränks. Ein aktiver Rabatt wird durch Uhrzeit-,
Wochentag- und (optional) Getränke-Filter definiert.

Rabatte gelten **nur** für Kartenzahlung mit Guthaben; Barzahlung und
Veranstaltungskarten bleiben unberührt (das erlaubt saubere Buchhaltung).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .database import get_connection

LOCAL_TZ = ZoneInfo("Europe/Berlin")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS discounts ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "name TEXT NOT NULL, "
    "percent INTEGER NOT NULL, "
    "start_time TEXT, "     # 'HH:MM' oder NULL für ganztägig
    "end_time TEXT, "
    "weekdays TEXT, "        # z. B. '1,2,3,4,5' (Mo=1 ... So=7) oder NULL
    "drink_ids TEXT, "       # JSON-Liste oder NULL = alle Getränke
    "active INTEGER NOT NULL DEFAULT 1"
    ")"
)


@dataclass
class Discount:
    id: int
    name: str
    percent: int
    start_time: str | None
    end_time: str | None
    weekdays: str | None
    drink_ids: str | None
    active: int = 1


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    own = False
    if conn is None:
        conn = get_connection()
        own = True
    conn.execute(_SCHEMA)
    conn.commit()
    if own:
        conn.close()


def _parse_time(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    try:
        h, m = hhmm.split(":", 1)
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _matches(d: Discount, drink_id: int, now: datetime) -> bool:
    if not d.active:
        return False
    if d.weekdays:
        try:
            days = {int(x) for x in d.weekdays.split(",") if x.strip()}
        except ValueError:
            days = set()
        if now.isoweekday() not in days:
            return False
    if d.drink_ids:
        try:
            ids = set(json.loads(d.drink_ids))
        except (ValueError, TypeError):
            ids = set()
        if ids and drink_id not in ids:
            return False
    start = _parse_time(d.start_time)
    end = _parse_time(d.end_time)
    if start is not None and end is not None:
        cur = now.hour * 60 + now.minute
        if start <= end:
            if not (start <= cur <= end):
                return False
        else:  # über Mitternacht
            if not (cur >= start or cur <= end):
                return False
    return True


def list_discounts() -> list[Discount]:
    with get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute("SELECT * FROM discounts ORDER BY name").fetchall()
        return [Discount(**row) for row in rows]


def add_discount(
    name: str,
    percent: int,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    weekdays: Iterable[int] | None = None,
    drink_ids: Iterable[int] | None = None,
    active: bool = True,
) -> int:
    with get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO discounts (name, percent, start_time, end_time, weekdays, drink_ids, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                max(0, min(100, int(percent))),
                start_time,
                end_time,
                ",".join(str(int(w)) for w in weekdays) if weekdays else None,
                json.dumps(sorted({int(x) for x in drink_ids})) if drink_ids else None,
                1 if active else 0,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def delete_discount(discount_id: int) -> None:
    with get_connection() as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM discounts WHERE id=?", (int(discount_id),))
        conn.commit()


def set_active(discount_id: int, active: bool) -> None:
    with get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE discounts SET active=? WHERE id=?", (1 if active else 0, int(discount_id))
        )
        conn.commit()


def active_discount_for(drink_id: int, when: datetime | None = None) -> Discount | None:
    """Liefert den *stärksten* Rabatt für ein Getränk zu gegebenem Zeitpunkt."""
    when = when or datetime.now(LOCAL_TZ)
    best: Discount | None = None
    for d in list_discounts():
        if not _matches(d, drink_id, when):
            continue
        if best is None or d.percent > best.percent:
            best = d
    return best


def effective_price(base_price: int, drink_id: int, when: datetime | None = None) -> int:
    """Runde den Preis nach Rabatt kaufmännisch auf ganze Cent."""
    d = active_discount_for(drink_id, when)
    if not d:
        return int(base_price)
    factor = (100 - int(d.percent)) / 100
    return max(0, int(round(base_price * factor)))
