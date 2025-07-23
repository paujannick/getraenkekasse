from dataclasses import dataclass
from typing import Optional
import sqlite3

from .database import get_connection, get_setting, set_setting

from . import rfid

# Maximum number of transactions to keep in the log
MAX_TRANSACTIONS = 10000


def get_overdraft_limit(conn: Optional[sqlite3.Connection] = None) -> int:
    """Return allowed negative balance in cents."""
    val = get_setting('overdraft_limit', conn)
    try:
        return int(val or '0')
    except ValueError:
        return 0


def set_overdraft_limit(limit_cents: int, conn: Optional[sqlite3.Connection] = None) -> None:
    set_setting('overdraft_limit', str(int(limit_cents)), conn)


def get_topup_uid(conn: Optional[sqlite3.Connection] = None) -> str | None:
    """Return the UID configured as top-up card."""
    return get_setting('topup_uid', conn)


def set_topup_uid(uid: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Store the UID of the special top-up card."""
    set_setting('topup_uid', uid, conn)



@dataclass
class User:
    id: int
    name: str
    rfid_uid: str
    balance: int  # in cents


@dataclass
class Drink:
    id: int
    name: str
    price: int  # in cents
    image: Optional[str]

    stock: int



def get_user_by_uid(uid: str) -> Optional[User]:
    try:
        with get_connection() as conn:
            cur = conn.execute('SELECT * FROM users WHERE rfid_uid = ?', (uid,))
            row = cur.fetchone()
        if row:
            return User(**row)
        return None
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen des Benutzers: {e}")
        return None


def update_balance(user_id: int, diff: int) -> bool:
    try:
        with get_connection() as conn:
            cur = conn.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
            row = cur.fetchone()
            if not row:
                return False
            new_balance = row['balance'] + diff
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
                'INSERT INTO transactions (user_id, drink_id, quantity) VALUES (?, ?, ?)',
                (user_id, drink_id, quantity))
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


def log_restock(drink_id: int, quantity: int) -> None:
    """Record a restock event."""
    try:
        with get_connection() as conn:
            conn.execute(
                'INSERT INTO restocks (drink_id, quantity) VALUES (?, ?)',
                (drink_id, quantity),
            )
            conn.commit()
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Schreiben der Auffüllung: {e}")


def get_restock_log(limit: int | None = None) -> list[sqlite3.Row]:
    try:
        with get_connection() as conn:
            query = (
                'SELECT r.timestamp, d.name as drink_name, r.quantity '
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
                'INSERT INTO topups (user_id, amount) VALUES (?, ?)',
                (user_id, amount),
            )
            conn.execute(
                'DELETE FROM topups WHERE id NOT IN ('
                'SELECT id FROM topups ORDER BY id DESC LIMIT 50)'
            )
            conn.commit()
    except sqlite3.Error as e:  # pragma: no cover - DB failure
        print(f"Fehler beim Schreiben der Aufladung: {e}")


def get_topup_log() -> list[sqlite3.Row]:
    try:
        with get_connection() as conn:
            cur = conn.execute(
                'SELECT t.timestamp, u.name as user_name, t.amount '
                'FROM topups t JOIN users u ON u.id = t.user_id '
                'ORDER BY t.timestamp DESC'
            )
            return cur.fetchall()
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Aufladungen: {e}")
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



def get_drinks(conn: Optional[sqlite3.Connection] = None, limit: int | None = None) -> list[Drink]:
    own = False
    try:
        if conn is None:
            conn = get_connection()
            own = True

        query = 'SELECT * FROM drinks ORDER BY name'
        if limit is not None:
            query += f' LIMIT {int(limit)}'
        cur = conn.execute(query)
        rows = [Drink(**row) for row in cur.fetchall()]
        return rows
    except sqlite3.Error as e:  # pragma: no cover
        print(f"Fehler beim Lesen der Getränke: {e}")
        return []
    finally:
        if own and conn is not None:
            conn.close()



def rfid_read_for_web() -> Optional[str]:
    """Read a UID for the web interface using the normal reader dialog."""
    return rfid.read_uid()

