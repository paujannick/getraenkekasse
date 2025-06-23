import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / 'data' / 'getraenkekasse.db'
REFRESH_FLAG = Path(__file__).resolve().parent.parent / 'data' / 'refresh.flag'

_SCHEMA = {
    'users': (
        'CREATE TABLE IF NOT EXISTS users ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'name TEXT NOT NULL, '
        'rfid_uid TEXT UNIQUE, '
        'balance INTEGER NOT NULL DEFAULT 0'
        ')'
    ),
    'drinks': (
        'CREATE TABLE IF NOT EXISTS drinks ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'name TEXT NOT NULL, '
        'price INTEGER NOT NULL, '
        'image TEXT, '
        'stock INTEGER NOT NULL DEFAULT 0'
        ')'
    ),
    'transactions': (
        'CREATE TABLE IF NOT EXISTS transactions ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'user_id INTEGER NOT NULL, '
        'drink_id INTEGER NOT NULL, '
        'quantity INTEGER NOT NULL, '
        'timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,'
        'FOREIGN KEY(user_id) REFERENCES users(id), '
        'FOREIGN KEY(drink_id) REFERENCES drinks(id)'
        ')'
    )
}

def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def touch_refresh_flag() -> None:
    """Create or update the refresh flag file."""
    REFRESH_FLAG.parent.mkdir(exist_ok=True)
    REFRESH_FLAG.touch()


def refresh_needed(last_mtime: float) -> bool:
    """Return True if the refresh flag has been modified since last_mtime."""
    if not REFRESH_FLAG.exists():
        return False
    return REFRESH_FLAG.stat().st_mtime > last_mtime


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    cursor = conn.cursor()
    for stmt in _SCHEMA.values():
        cursor.execute(stmt)
    conn.commit()
    add_sample_data(conn)
    if own_conn:
        conn.close()


def add_sample_data(conn: sqlite3.Connection) -> None:
    """Insert a few example users and drinks if tables are empty."""
    cur = conn.execute('SELECT COUNT(*) FROM users')
    if cur.fetchone()[0] == 0:
        conn.execute(
            'INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)',
            ('Alice', 'TESTCARD123', 1000))
        conn.execute(
            'INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)',
            ('Bob', 'TESTCARD456', 500))

    cur = conn.execute('SELECT COUNT(*) FROM drinks')
    if cur.fetchone()[0] == 0:
        conn.execute(
            'INSERT INTO drinks (name, price, stock) VALUES (?, ?, ?)',
            ('Wasser', 150, 20))
        conn.execute(
            'INSERT INTO drinks (name, price, stock) VALUES (?, ?, ?)',
            ('Cola', 200, 15))

    conn.commit()
