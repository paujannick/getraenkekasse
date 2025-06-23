from dataclasses import dataclass
from typing import Optional
import sqlite3

from .database import get_connection

from . import rfid



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
    conn = get_connection()
    cur = conn.execute('SELECT * FROM users WHERE rfid_uid = ?', (uid,))
    row = cur.fetchone()
    conn.close()
    if row:
        return User(**row)
    return None


def update_balance(user_id: int, diff: int) -> bool:
    conn = get_connection()
    cur = conn.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    new_balance = row['balance'] + diff
    if new_balance < 0:
        conn.close()
        return False
    conn.execute('UPDATE users SET balance = ? WHERE id = ?', (new_balance, user_id))
    conn.commit()
    conn.close()
    return True


def add_transaction(user_id: int, drink_id: int, quantity: int) -> None:
    """Store a purchase in the transaction log."""
    conn = get_connection()
    conn.execute(
        'INSERT INTO transactions (user_id, drink_id, quantity) VALUES (?, ?, ?)',
        (user_id, drink_id, quantity))
    conn.commit()
    conn.close()



def get_drinks(conn: Optional[sqlite3.Connection] = None, limit: int | None = None) -> list[Drink]:

    own = False
    if conn is None:
        conn = get_connection()
        own = True

    query = 'SELECT * FROM drinks ORDER BY name'
    if limit is not None:
        query += f' LIMIT {int(limit)}'
    cur = conn.execute(query)

    rows = [Drink(**row) for row in cur.fetchall()]
    if own:
        conn.close()
    return rows



def rfid_read_for_web() -> Optional[str]:
    """Read a UID for the web interface (simulated)."""
    return rfid.read_uid_cli()

