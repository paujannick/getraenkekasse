"""Kassenbuch-Kern: Konten + doppelseitige Buchungen mit Belegablage.

Konzept
-------
Jede Buchung ist ein **Transfer** zwischen zwei Konten. Fehlt eine Seite
(z. B. Barverkauf – der Kunde hat kein Konto), steht dort ``NULL``. Der
Kontostand ergibt sich aus:

    balance(a) = Σ Zugänge (to=a) − Σ Abgänge (from=a)

Automatisch gebucht wird von den betreffenden Modulen:

* **Barverkauf** (`sale_cash`)  – Kunde  → terminal_cash *und*
                                  terminal_cash → income_drinks
                                  (wir schreiben nur den ersten Leg;
                                  der zweite ergibt sich implizit, weil
                                  income_drinks in Anlehnung an
                                  Einzelabschluss über Verkauf zählt.)
* **Kartenverkauf**             – kein Bargeldstrom, nur income_drinks.
* **Aufladung bar** (`topup_cash`) – Kunde → terminal_cash (Erklärt, wo
                                     das Bargeld hingeht).
* **Kasse leeren**              – terminal_cash → main_cash.
* **Einkauf**                   – main_cash/bank → expenses_shopping.
* **Sonst. Einnahme/Ausgabe**   – frei wählbar.

Belege werden nach ``data/receipts/`` gelegt (secure_filename), Pfad wird
mit der Buchung verknüpft.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import audit, security
from .database import DB_PATH, get_connection

_LOG = logging.getLogger(__name__)

RECEIPT_DIR = DB_PATH.parent / "receipts"
ALLOWED_RECEIPT_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"})


LEDGER_KINDS = {
    "sale_cash":  "Barverkauf",
    "sale_card":  "Kartenverkauf",
    "topup_cash": "Aufladung (Bar)",
    "topup_bank": "Aufladung (Überweisung)",
    "transfer":   "Umbuchung / Kasse leeren",
    "expense":    "Ausgabe",
    "income":     "Einnahme",
    "closing":    "Kassensturz-Anpassung",
    "reversal":   "Storno",
    "other":      "Sonstige Buchung",
}


@dataclass
class Account:
    id: int
    code: str
    name: str
    kind: str
    system: int
    sort_order: int
    note: Optional[str]
    hidden: int = 0


@dataclass
class LedgerEntry:
    id: int
    timestamp: str
    kind: str
    from_account_id: Optional[int]
    to_account_id: Optional[int]
    amount_cents: int
    ref: Optional[str]
    actor: Optional[str]
    note: Optional[str]
    receipt_path: Optional[str]
    reversed_of: Optional[int]


# ---------------------------------------------------------------------------
# Konto-Verwaltung
# ---------------------------------------------------------------------------

def list_accounts(kind: Optional[str] = None, *, include_hidden: bool = False) -> list[Account]:
    q = "SELECT * FROM accounts WHERE 1=1 "
    params: list = []
    if not include_hidden:
        q += "AND COALESCE(hidden, 0)=0 "
    if kind:
        q += "AND kind=? "
        params.append(kind)
    q += "ORDER BY sort_order, name"
    with get_connection() as conn:
        try:
            rows = conn.execute(q, params).fetchall()
        except sqlite3.OperationalError:
            return []
    return [Account(**{k: r[k] for k in r.keys() if k in Account.__dataclass_fields__}) for r in rows]


def get_account(id_or_code) -> Optional[Account]:
    with get_connection() as conn:
        if isinstance(id_or_code, int) or (isinstance(id_or_code, str) and id_or_code.isdigit()):
            r = conn.execute("SELECT * FROM accounts WHERE id=?", (int(id_or_code),)).fetchone()
        else:
            r = conn.execute("SELECT * FROM accounts WHERE code=?", (str(id_or_code),)).fetchone()
    if not r:
        return None
    return Account(**{k: r[k] for k in r.keys() if k in Account.__dataclass_fields__})


def create_account(code: str, name: str, kind: str = "cash", note: str | None = None) -> Account:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO accounts (code, name, kind, system, note) VALUES (?, ?, ?, 0, ?)",
            (code.strip(), name.strip(), kind, note),
        )
        conn.commit()
    return get_account(code)  # type: ignore[return-value]


def delete_account(account_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT system FROM accounts WHERE id=?", (int(account_id),)).fetchone()
        if not row or row["system"]:
            return False
        # Konto darf nur weg, wenn keine Buchungen mehr existieren.
        c = conn.execute(
            "SELECT COUNT(*) c FROM ledger_entries WHERE from_account_id=? OR to_account_id=?",
            (int(account_id), int(account_id)),
        ).fetchone()["c"]
        if c > 0:
            return False
        conn.execute("DELETE FROM accounts WHERE id=?", (int(account_id),))
        conn.commit()
    return True


def rename_account(account_id: int, name: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET name=? WHERE id=?", (name.strip(), int(account_id)))
        conn.commit()


# ---------------------------------------------------------------------------
# Salden
# ---------------------------------------------------------------------------

def balance_of(account_id: int) -> int:
    with get_connection() as conn:
        r = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN to_account_id=? THEN amount_cents "
            "                         WHEN from_account_id=? THEN -amount_cents ELSE 0 END), 0) AS b "
            "FROM ledger_entries WHERE from_account_id=? OR to_account_id=?",
            (int(account_id), int(account_id), int(account_id), int(account_id)),
        ).fetchone()
    return int(r["b"] or 0)


def balances() -> list[tuple[Account, int]]:
    return [(a, balance_of(a.id)) for a in list_accounts()]


# ---------------------------------------------------------------------------
# Buchen
# ---------------------------------------------------------------------------

def _receipt_target(original_name: str) -> Optional[Path]:
    from werkzeug.utils import secure_filename
    clean = secure_filename(original_name or "")
    if not clean:
        return None
    ext = Path(clean).suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXTS:
        return None
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    return RECEIPT_DIR / f"{uuid.uuid4().hex}{ext}"


def save_receipt(file_storage) -> Optional[str]:
    """Speichert einen Beleg aus einem Flask-``FileStorage`` und liefert den Pfad."""
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None
    target = _receipt_target(file_storage.filename)
    if not target:
        return None
    file_storage.save(target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    # Nur relativer Pfad gespeichert; Zugriff später über /kassenbuch/beleg/<id>.
    return str(target.relative_to(DB_PATH.parent))


def post(
    kind: str,
    amount_cents: int,
    *,
    from_account: int | str | None = None,
    to_account: int | str | None = None,
    ref: str | None = None,
    actor: str | None = None,
    note: str | None = None,
    receipt_path: str | None = None,
) -> int:
    """Eine Buchung schreiben. Beträge werden immer positiv gespeichert."""
    if amount_cents <= 0:
        raise ValueError("Betrag muss > 0 sein")
    if not from_account and not to_account:
        raise ValueError("Mindestens ein Konto muss beteiligt sein")

    def _resolve(x):
        if x is None:
            return None
        a = get_account(x)
        if not a:
            raise ValueError(f"Konto '{x}' nicht gefunden")
        return a.id

    from_id = _resolve(from_account)
    to_id = _resolve(to_account)

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO ledger_entries "
            "(kind, from_account_id, to_account_id, amount_cents, ref, actor, note, receipt_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, from_id, to_id, int(amount_cents), ref, actor, note, receipt_path),
        )
        conn.commit()
        entry_id = int(cur.lastrowid)

    audit.log("ledger.post", actor=actor, target=kind,
              details=f"{amount_cents}ct from={from_id} to={to_id} ref={ref}")
    return entry_id


def reverse(entry_id: int, actor: str | None = None) -> int:
    """Storniert eine Buchung mit spiegelbildlichem Eintrag."""
    with get_connection() as conn:
        r = conn.execute("SELECT * FROM ledger_entries WHERE id=?", (int(entry_id),)).fetchone()
        if not r:
            raise ValueError("Buchung nicht gefunden")
        cur = conn.execute(
            "INSERT INTO ledger_entries "
            "(kind, from_account_id, to_account_id, amount_cents, ref, actor, note, reversed_of) "
            "VALUES ('reversal', ?, ?, ?, ?, ?, ?, ?)",
            (r["to_account_id"], r["from_account_id"], r["amount_cents"],
             r["ref"], actor, f"Storno #{entry_id}", entry_id),
        )
        conn.commit()
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Abfragen
# ---------------------------------------------------------------------------

def entries(
    *,
    account_id: int | None = None,
    kind: str | None = None,
    limit: int = 200,
    since: str | None = None,
) -> list[dict]:
    q = (
        "SELECT l.*, af.code AS from_code, af.name AS from_name, "
        "at.code AS to_code, at.name AS to_name "
        "FROM ledger_entries l "
        "LEFT JOIN accounts af ON af.id=l.from_account_id "
        "LEFT JOIN accounts at ON at.id=l.to_account_id "
        "WHERE 1=1 "
    )
    params: list = []
    if account_id is not None:
        q += "AND (l.from_account_id=? OR l.to_account_id=?) "
        params += [int(account_id), int(account_id)]
    if kind:
        q += "AND l.kind=? "
        params.append(kind)
    if since:
        q += "AND l.timestamp >= ? "
        params.append(since)
    q += "ORDER BY l.id DESC LIMIT ?"
    params.append(int(limit))
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def sum_between(account_id: int, since: str | None, until: str | None = None) -> int:
    q = (
        "SELECT COALESCE(SUM(CASE WHEN to_account_id=? THEN amount_cents "
        "                         WHEN from_account_id=? THEN -amount_cents ELSE 0 END), 0) AS b "
        "FROM ledger_entries WHERE (from_account_id=? OR to_account_id=?) "
    )
    params: list = [int(account_id)] * 4
    if since:
        q += "AND timestamp >= ? "
        params.append(since)
    if until:
        q += "AND timestamp <= ? "
        params.append(until)
    with get_connection() as conn:
        r = conn.execute(q, params).fetchone()
    return int(r["b"] or 0)


# ---------------------------------------------------------------------------
# Convenience: Auto-Buchungen aus Kiosk-Modul
# ---------------------------------------------------------------------------

def on_cash_sale(amount_cents: int, ref: str | None = None, actor: str | None = None) -> None:
    """Barverkauf → Bargeld in die Kühlschrank-Kasse (kein separates Erlöskonto)."""
    try:
        post("sale_cash", amount_cents, to_account="terminal_cash",
             ref=ref, actor=actor, note="Barverkauf (Kunde → Kühlschrank-Kasse)")
    except (sqlite3.OperationalError, ValueError):
        pass


def on_card_sale(amount_cents: int, ref: str | None = None, actor: str | None = None) -> None:
    """Kartenverkauf: keine Kassenbewegung – nur Guthaben wird intern verrechnet."""
    return


def on_cash_topup(amount_cents: int, ref: str | None = None, actor: str | None = None) -> None:
    try:
        post("topup_cash", amount_cents, to_account="terminal_cash",
             ref=ref, actor=actor, note="Guthaben-Aufladung bar (Kunde → Kühlschrank-Kasse)")
    except (sqlite3.OperationalError, ValueError):
        pass


def transfer_terminal_to_main(amount_cents: int, actor: str | None = None,
                              note: str | None = None) -> int:
    return post("transfer", amount_cents,
                from_account="terminal_cash", to_account="main_cash",
                actor=actor, note=note or "Kasse geleert (Terminal → Hauptkasse)")


__all__ = [
    "Account", "LedgerEntry", "LEDGER_KINDS",
    "list_accounts", "get_account", "create_account", "delete_account", "rename_account",
    "balance_of", "balances", "post", "reverse", "entries", "sum_between",
    "save_receipt", "RECEIPT_DIR",
    "on_cash_sale", "on_card_sale", "on_cash_topup", "transfer_terminal_to_main",
]
