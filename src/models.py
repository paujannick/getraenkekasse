from dataclasses import dataclass
from typing import Optional
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .database import get_connection, get_setting, set_setting

from . import rfid

# Maximum number of transactions to keep in the log
MAX_TRANSACTIONS = 10000


LOCAL_TZ = ZoneInfo("Europe/Berlin")


def _now() -> str:
    """Return the current local time as ISO string."""
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def get_overdraft_limit(conn: Optional[sqlite3.Connection] = None) -> int:
    """Return allowed negative balance in cents."""
    val = get_setting('overdraft_limit', conn)
    try:
        return int(val or '0')
    except ValueError:
        return 0


def set_overdraft_limit(limit_cents: int, conn: Optional[sqlite3.Connection] = None) -> None:
    set_setting('overdraft_limit', str(int(limit_cents)), conn)


def get_admin_pin(conn: Optional[sqlite3.Connection] = None) -> str:
    """Return the admin PIN used for the GUI."""
    return get_setting('admin_pin', conn) or '1234'


def set_admin_pin(pin: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Store the admin PIN."""
    set_setting('admin_pin', pin, conn)


def get_telegram_token(conn: Optional[sqlite3.Connection] = None) -> str:
    """Return the Telegram bot token."""
    return get_setting('telegram_token', conn) or ''


def set_telegram_token(token: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Store the Telegram bot token."""
    set_setting('telegram_token', token, conn)


def get_telegram_chat(conn: Optional[sqlite3.Connection] = None) -> str:
    """Return the Telegram chat id for notifications."""
    return get_setting('telegram_chat', conn) or ''


def set_telegram_chat(chat_id: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Store the Telegram chat id."""
    set_setting('telegram_chat', chat_id, conn)



@dataclass
class User:
    id: int
    name: str
    rfid_uid: str
    balance: int  # in cents
    is_event: int = 0
    active: int = 1
    show_on_payment: int = 0
    is_admin: int = 0
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class Drink:
    id: int
    name: str
    price: int  # in cents
    image: Optional[str]

    stock: int
    min_stock: int
    page: int



def get_user_by_uid(uid: str) -> Optional[User]:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                'SELECT * FROM users WHERE rfid_uid = ? AND active = 1 '
                'AND (valid_from IS NULL OR valid_from <= DATE("now")) '
                'AND (valid_until IS NULL OR valid_until >= DATE("now"))',
                (uid,),
            )
            row = cur.fetchone()
        if row:
            return User(**row)
        return None
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen des Benutzers: {e}")
        return None


def get_user(user_id: int) -> Optional[User]:
    """Return a user by their database id."""
    try:
        with get_connection() as conn:
            cur = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            row = cur.fetchone()
        if row:
            return User(**row)
        return None
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen des Benutzers: {e}")
        return None


def get_event_payment_users() -> list[User]:
    """Return active event users that should show as payment method."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                'SELECT * FROM users WHERE is_event=1 AND show_on_payment=1 AND active=1 '
                'AND (valid_from IS NULL OR valid_from <= DATE("now")) '
                'AND (valid_until IS NULL OR valid_until >= DATE("now")) '
                'ORDER BY name'
            )
            rows = cur.fetchall()
        return [User(**row) for row in rows]
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Veranstaltungskarten: {e}")
        return []


def update_balance(user_id: int, diff: int) -> bool:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                'SELECT balance, is_event, active FROM users WHERE id = ? '
                'AND (valid_from IS NULL OR valid_from <= DATE("now")) '
                'AND (valid_until IS NULL OR valid_until >= DATE("now"))',
                (user_id,),
            )
            row = cur.fetchone()
            if not row or row['active'] == 0:
                return False
            new_balance = row['balance'] + diff
            if row['is_event'] == 0:
                limit = get_overdraft_limit(conn)
                if new_balance < -limit:
                    return False
            conn.execute('UPDATE users SET balance = ? WHERE id = ?', (new_balance, user_id))
            conn.commit()
        return True
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Aktualisieren des Guthabens: {e}")
        return False


def add_transaction(user_id: int, drink_id: int, quantity: int) -> None:
    """Store a purchase in the transaction log."""
    try:
        with get_connection() as conn:
            conn.execute(
                'INSERT INTO transactions (user_id, drink_id, quantity, timestamp) '
                'VALUES (?, ?, ?, ?)',
                (user_id, drink_id, quantity, _now()),
            )
            conn.execute(
                'DELETE FROM transactions WHERE id NOT IN ('
                'SELECT id FROM transactions ORDER BY id DESC LIMIT ?)',
                (MAX_TRANSACTIONS,))
            conn.commit()
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Schreiben der Transaktion: {e}")


def update_drink_stock(drink_id: int, diff: int) -> bool:
    """Increase or decrease drink stock. Returns False if not enough stock."""
    try:
        with get_connection() as conn:
            cur = conn.execute('SELECT stock FROM drinks WHERE id = ?', (drink_id,))
            row = cur.fetchone()
            if row is None:
                return False
            new_stock = row['stock'] + diff
            conn.execute('UPDATE drinks SET stock = ? WHERE id = ?', (new_stock, drink_id))
            conn.commit()
            from . import database
            database.touch_refresh_flag()
        return True
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Aktualisieren des Lagerbestands: {e}")
        return False



def get_cash_user_id(conn: Optional[sqlite3.Connection] = None) -> int:
    """Ensure a special user for cash payments exists and return its id."""
    own = False
    if conn is None:
        conn = get_connection()
        own = True
    cur = conn.execute("SELECT id FROM users WHERE name='BARZAHLUNG'")
    row = cur.fetchone()
    if row:
        uid = row['id']
    else:
        conn.execute(
            "INSERT INTO users (name, rfid_uid, balance) VALUES ('BARZAHLUNG', 'CASH', 0)"
        )
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE name='BARZAHLUNG'").fetchone()['id']
    if own:
        conn.close()
    return uid


def log_restock(drink_id: int, quantity: int) -> None:
    """Record a restock event."""
    try:
        with get_connection() as conn:
            conn.execute(
                'INSERT INTO restocks (drink_id, quantity, timestamp) '
                'VALUES (?, ?, ?)',
                (drink_id, quantity, _now()),
            )
            conn.commit()
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Schreiben der Auffüllung: {e}")


def get_restock_log(limit: int | None = None) -> list[sqlite3.Row]:
    try:
        with get_connection() as conn:
            query = (
                'SELECT r.id, r.timestamp, d.name as drink_name, r.quantity '
                'FROM restocks r JOIN drinks d ON d.id = r.drink_id '
                'ORDER BY r.timestamp DESC'
            )
            if limit is not None:
                query += f' LIMIT {int(limit)}'
            cur = conn.execute(query)
            return cur.fetchall()
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Auffüllungen: {e}")
        return []


def add_topup(user_id: int, amount: int) -> None:
    """Store a top-up event and keep only the most recent 50."""
    try:
        with get_connection() as conn:
            conn.execute(
                'INSERT INTO topups (user_id, amount, timestamp) '
                'VALUES (?, ?, ?)',
                (user_id, amount, _now()),
            )
            conn.execute(
                'DELETE FROM topups WHERE id NOT IN ('
                'SELECT id FROM topups ORDER BY id DESC LIMIT 50)'
            )
            conn.commit()
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Schreiben der Aufladung: {e}")


def reset_event_card(user_id: int) -> None:
    """Delete all transactions and validity dates for an event card."""
    try:
        with get_connection() as conn:
            conn.execute('DELETE FROM transactions WHERE user_id=?', (user_id,))
            conn.execute(
                'UPDATE users SET balance=0, valid_from=NULL, valid_until=NULL '
                'WHERE id=? AND is_event=1',
                (user_id,),
            )
            conn.commit()
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Zurücksetzen der Veranstaltungskarte: {e}")


def get_topup_log() -> list[sqlite3.Row]:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                'SELECT t.id, t.timestamp, u.name as user_name, t.amount '
                'FROM topups t JOIN users u ON u.id = t.user_id '
                'ORDER BY t.timestamp DESC'
            )
            return cur.fetchall()
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Aufladungen: {e}")
        return []


def get_transaction_log(limit: int | None = None) -> list[sqlite3.Row]:
    """Return an anonymized list of sales transactions."""
    try:
        with get_connection() as conn:
            query = (
                "SELECT t.timestamp, d.name as drink_name, t.quantity "
                "FROM transactions t "
                "JOIN drinks d ON d.id = t.drink_id "
                "ORDER BY t.timestamp DESC"
            )
            if limit is not None:
                query += f" LIMIT {int(limit)}"
            cur = conn.execute(query)
            return cur.fetchall()
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Verkäufe: {e}")
        return []


def get_drink_by_id(drink_id: int) -> Optional[Drink]:
    try:
        with get_connection() as conn:
            cur = conn.execute('SELECT * FROM drinks WHERE id = ?', (drink_id,))
            row = cur.fetchone()
        if row:
            return Drink(**row)
        return None
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen des Getränks: {e}")
        return None



def get_drinks(conn: Optional[sqlite3.Connection] = None, limit: int | None = None, page: int | None = None) -> list[Drink]:
    own = False
    try:
        if conn is None:
            conn = get_connection()
            own = True

        query = 'SELECT * FROM drinks'
        params: list = []
        if page is not None:
            query += ' WHERE page=?'
            params.append(page)
        query += ' ORDER BY name'
        if limit is not None:
            query += f' LIMIT {int(limit)}'
        cur = conn.execute(query, params)
        rows = [Drink(**row) for row in cur.fetchall()]
        return rows
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Getränke: {e}")
        return []
    finally:
        if own and conn is not None:
            conn.close()


def get_max_page(conn: Optional[sqlite3.Connection] = None) -> int:
    """Return the highest page number in the drinks table."""
    own = False
    try:
        if conn is None:
            conn = get_connection()
            own = True
        cur = conn.execute('SELECT MAX(page) FROM drinks')
        row = cur.fetchone()
        return row[0] or 1
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Seitenzahl: {e}")
        return 1
    finally:
        if own and conn is not None:
            conn.close()


def get_drinks_below_min(conn: Optional[sqlite3.Connection] = None) -> list[Drink]:
    """Return drinks where stock is below the configured minimum."""
    own = False
    try:
        if conn is None:
            conn = get_connection()
            own = True
        cur = conn.execute('SELECT * FROM drinks WHERE stock < min_stock ORDER BY name')
        return [Drink(**row) for row in cur.fetchall()]
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Mindestbestände: {e}")
        return []
    finally:
        if own and conn is not None:
            conn.close()



def rfid_read_for_web() -> Optional[str]:
    """Read a UID for the web interface using the normal reader dialog."""
    return rfid.read_uid()


def _month_list(months: int) -> list[str]:
    """Return a list of year-month strings for the last ``months`` months."""
    from datetime import date

    today = date.today()
    res: list[str] = []
    y = today.year
    m = today.month
    for _ in range(months):
        res.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(res))


def get_monthly_stats(months: int = 12) -> tuple[list[dict[str, int]], dict[str, int]]:
    """Return statistics for the last ``months`` months."""
    if months <= 0:
        return [], {}

    month_strings = _month_list(months)
    start = month_strings[0] + "-01"

    conn = get_connection()
    try:
        topup_rows = conn.execute(
            "SELECT strftime('%Y-%m', timestamp) AS ym, "
            "SUM(amount) AS total FROM topups WHERE timestamp >= ? "
            "GROUP BY ym",
            (start,),
        ).fetchall()
        topups = {row["ym"]: row["total"] for row in topup_rows}

        cash_rows = conn.execute(
            "SELECT strftime('%Y-%m', t.timestamp) AS ym, "
            "SUM(t.quantity) AS cnt, "
            "SUM(t.quantity * d.price) AS val "
            "FROM transactions t "
            "JOIN drinks d ON d.id = t.drink_id "
            "JOIN users u ON u.id = t.user_id "
            "WHERE u.name='BARZAHLUNG' AND t.timestamp >= ? "
            "GROUP BY ym",
            (start,),
        ).fetchall()
        cash = {row["ym"]: {"cnt": row["cnt"], "val": row["val"]} for row in cash_rows}

        card_rows = conn.execute(
            "SELECT strftime('%Y-%m', t.timestamp) AS ym, "
            "SUM(t.quantity) AS cnt, "
            "SUM(t.quantity * d.price) AS val "
            "FROM transactions t "
            "JOIN drinks d ON d.id = t.drink_id "
            "JOIN users u ON u.id = t.user_id "
            "WHERE u.name!='BARZAHLUNG' AND t.timestamp >= ? "
            "GROUP BY ym",
            (start,),
        ).fetchall()
        card = {row["ym"]: {"cnt": row["cnt"], "val": row["val"]} for row in card_rows}

        stats: list[dict[str, int]] = []
        totals = {
            "topup": 0,
            "cash_count": 0,
            "cash_value": 0,
            "card_count": 0,
            "card_value": 0,
        }
        for ym in month_strings:
            row = {
                "month": ym,
                "topup": int(topups.get(ym, 0) or 0),
                "cash_count": int(cash.get(ym, {}).get("cnt", 0) or 0),
                "cash_value": int(cash.get(ym, {}).get("val", 0) or 0),
                "card_count": int(card.get(ym, {}).get("cnt", 0) or 0),
                "card_value": int(card.get(ym, {}).get("val", 0) or 0),
            }
            for k in ("topup", "cash_count", "cash_value", "card_count", "card_value"):
                totals[k] += row[k]
            stats.append(row)

        totals["all_value"] = totals["topup"] + totals["cash_value"] + totals["card_value"]
        return stats, totals
    finally:
        conn.close()

