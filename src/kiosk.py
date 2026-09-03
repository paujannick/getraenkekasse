"""Kassen-Backend: Warenkorb-Buchung inkl. Rabatt, Bestand, Guthaben."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from dataclasses import dataclass

from . import activity, ledger, models, webhooks
from .database import get_connection
from .database import get_connection as _gc
from .discounts import active_discount_for, effective_price

_LOG = logging.getLogger(__name__)


@dataclass
class LineItem:
    drink_id: int
    quantity: int


@dataclass
class BookingResult:
    ok: bool
    user_name: str = ""
    total_cents: int = 0
    new_balance_cents: int | None = None
    error: str = ""
    items: list[dict] | None = None
    receipt_ref: str | None = None


def _price_line(drink_id: int, qty: int) -> tuple[int, int, str]:
    drink = models.get_drink_by_id(drink_id)
    if not drink:
        raise ValueError(f"unbekanntes Getränk #{drink_id}")
    unit = effective_price(drink.price, drink.id)
    disc = active_discount_for(drink.id)
    return unit, unit * qty, drink.name if not disc else f"{drink.name} ({disc.percent}%)"


def book(uid: str, cart: list[LineItem]) -> BookingResult:
    uid = (uid or "").strip()
    if not uid or not cart:
        return BookingResult(False, error="Leerer Warenkorb")

    user = models.get_user_by_uid(uid)
    if user is None:
        return BookingResult(False, error="Karte unbekannt")

    # 1) Preise + Zeilen berechnen
    lines: list[dict] = []
    total = 0
    for li in cart:
        try:
            unit, subtotal, label = _price_line(li.drink_id, li.quantity)
        except ValueError as e:
            return BookingResult(False, error=str(e))
        lines.append(
            {"drink_id": li.drink_id, "quantity": li.quantity,
             "unit_cents": unit, "subtotal_cents": subtotal, "label": label}
        )
        total += subtotal

    # 2) Bestand prüfen
    with get_connection() as conn:
        for li in cart:
            row = conn.execute(
                "SELECT stock, name FROM drinks WHERE id=?", (li.drink_id,)
            ).fetchone()
            if not row:
                return BookingResult(False, error=f"Getränk #{li.drink_id} fehlt")
            if row["stock"] < li.quantity:
                return BookingResult(
                    False,
                    error=f"Nicht genug '{row['name']}' auf Lager ({row['stock']} statt {li.quantity})",
                )

    # 3) Guthaben ggf. abbuchen (Bar-Karte und Event-Karten ausgenommen)
    is_cash = user.name.upper() == "BARZAHLUNG" or user.rfid_uid == "CASH"
    if user.is_event == 0 and not is_cash:
        if not models.update_balance(user.id, -total):
            return BookingResult(False, user_name=user.name,
                                 error="Guthaben reicht nicht")

    # 4) Bestand + Transaktionen schreiben
    receipt_ref = f"R{secrets.token_urlsafe(8)}"
    try:
        for li in cart:
            if not models.update_drink_stock(li.drink_id, -li.quantity):
                raise RuntimeError("Bestand nicht aktualisierbar")
            models.add_transaction(user.id, li.drink_id, li.quantity)
        # Ref auf die eben erstellten Zeilen setzen (best effort).
        with _gc() as conn:
            try:
                conn.execute(
                    "UPDATE transactions SET receipt_ref=? "
                    "WHERE receipt_ref IS NULL AND user_id=? "
                    "  AND id > (SELECT COALESCE(MAX(id), 0) - ? FROM transactions)",
                    (receipt_ref, user.id, len(cart)),
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass
    except Exception as e:
        # Rollback Guthaben
        _LOG.error("booking failed, rolling back: %s", e)
        if user.is_event == 0 and not is_cash:
            models.update_balance(user.id, total)
        return BookingResult(False, error=str(e))

    fresh = models.get_user(user.id)
    new_balance = fresh.balance if fresh and user.is_event == 0 else None

    activity.log(
        "purchase",
        actor=user.name,
        target=", ".join(f"{l['quantity']}x{l['label']}" for l in lines),
        amount_cents=total,
    )

    # Kassenbuch: Barverkauf ⇒ Terminal-Kasse & Erlöskonto; Kartenverkauf nur Erlös.
    ref = f"purchase user={user.id}"
    if is_cash:
        ledger.on_cash_sale(total, ref=ref, actor=user.name)
    elif user.is_event == 0:
        ledger.on_card_sale(total, ref=ref, actor=user.name)
    try:
        stock_flags = []
        with get_connection() as conn:
            for li in cart:
                row = conn.execute(
                    "SELECT name, stock, min_stock FROM drinks WHERE id=?", (li.drink_id,)
                ).fetchone()
                if row and row["stock"] < row["min_stock"]:
                    stock_flags.append(f"{row['name']} ({row['stock']}/{row['min_stock']})")
        if stock_flags:
            webhooks.dispatch(
                "low_stock",
                {"text": "🟡 Bestand niedrig: " + ", ".join(stock_flags)},
            )
        if total >= 2000:
            webhooks.dispatch(
                "purchase_over_x",
                {"text": f"💶 Großkauf {total/100:.2f} € von {user.name}"},
            )
    except sqlite3.Error:
        pass

    return BookingResult(
        ok=True,
        user_name=user.name,
        total_cents=total,
        new_balance_cents=new_balance,
        items=lines,
        receipt_ref=receipt_ref,
    )


def reverse_booking(receipt_ref: str, actor: str | None = None) -> dict:
    """Kauf-Storno: Bestand zurück, Guthaben/Bargeld zurück, Kassenbuch-Ausgleich."""
    with _gc() as conn:
        rows = conn.execute(
            "SELECT t.id, t.quantity, t.user_id, u.name AS user_name, u.rfid_uid, u.is_event, "
            "       t.drink_id, d.name AS drink_name, d.price "
            "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
            "JOIN users u ON u.id=t.user_id "
            "WHERE t.receipt_ref=? ORDER BY t.id",
            (receipt_ref,),
        ).fetchall()
    if not rows:
        return {"ok": False, "error": "Kauf nicht gefunden"}

    user_id = rows[0]["user_id"]
    is_cash = rows[0]["user_name"].upper() == "BARZAHLUNG" or rows[0]["rfid_uid"] == "CASH"
    is_event = bool(rows[0]["is_event"])
    total = sum(r["quantity"] * r["price"] for r in rows)

    # Bestand zurueck
    for r in rows:
        models.update_drink_stock(r["drink_id"], r["quantity"])

    # Guthaben zurueck (nur bei Guthaben-Karten)
    if not is_cash and not is_event:
        models.update_balance(user_id, total)

    with _gc() as conn:
        conn.execute("DELETE FROM transactions WHERE receipt_ref=?", (receipt_ref,))
        conn.commit()

    # Kassenbuch-Gegenbuchung nur beim Barverkauf – Karten/Events berühren
    # das Kassenbuch nicht (Guthaben wurde oben zurückgebucht).
    try:
        if is_cash:
            ledger.post("reversal", total,
                        from_account="terminal_cash", to_account=None,
                        ref=f"reverse {receipt_ref}", actor=actor,
                        note=f"Storno Barkauf {receipt_ref}")
    except (sqlite3.OperationalError, ValueError):
        pass

    activity.log("purchase.reversed",
                 actor=actor or "kiosk",
                 target=", ".join(f"{r['quantity']}x{r['drink_name']}" for r in rows),
                 amount_cents=-total,
                 note=f"ref={receipt_ref}")
    return {"ok": True, "total_cents": total, "user_name": rows[0]["user_name"]}


def recent_receipts(limit: int = 5) -> list[dict]:
    """Letzte Kauf-Refs mit Zusammenfassung."""
    with _gc() as conn:
        try:
            rows = conn.execute(
                "SELECT t.receipt_ref, MAX(t.id) AS mx, MAX(t.timestamp) AS ts, "
                "MAX(u.name) AS user_name, "
                "SUM(t.quantity * d.price) AS total, "
                "GROUP_CONCAT(t.quantity || 'x ' || d.name, ', ') AS items "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "JOIN users u ON u.id=t.user_id "
                "WHERE t.receipt_ref IS NOT NULL "
                "GROUP BY t.receipt_ref ORDER BY mx DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {"ref": r["receipt_ref"], "ts": r["ts"], "user": r["user_name"],
         "total": int(r["total"] or 0), "items": r["items"] or ""}
        for r in rows
    ]
