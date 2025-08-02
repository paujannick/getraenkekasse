import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / 'data' / 'getraenkekasse.db'

REFRESH_FLAG = Path(__file__).resolve().parent.parent / 'data' / 'refresh.flag'
# File that signals the application to terminate
EXIT_FLAG = Path(__file__).resolve().parent.parent / 'data' / 'exit.flag'


_SCHEMA = {
    'users': (
        'CREATE TABLE IF NOT EXISTS users ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'name TEXT NOT NULL, '
        'rfid_uid TEXT UNIQUE, '
        'balance INTEGER NOT NULL DEFAULT 0, '
        'is_invoice INTEGER NOT NULL DEFAULT 0, '
        'active INTEGER NOT NULL DEFAULT 1'
        ')'
    ),
    'drinks': (
        'CREATE TABLE IF NOT EXISTS drinks ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'name TEXT NOT NULL, '
        'price INTEGER NOT NULL, '

        'image TEXT, '
        'stock INTEGER NOT NULL DEFAULT 0, '
        'min_stock INTEGER NOT NULL DEFAULT 0, '
        'page INTEGER NOT NULL DEFAULT 1'

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
    ),
    'restocks': (
        'CREATE TABLE IF NOT EXISTS restocks ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'drink_id INTEGER NOT NULL, '
        'quantity INTEGER NOT NULL, '
        'timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,'
        'FOREIGN KEY(drink_id) REFERENCES drinks(id)'
        ')'
    ),
    'topups': (
        'CREATE TABLE IF NOT EXISTS topups ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'user_id INTEGER NOT NULL, '
        'amount INTEGER NOT NULL, '
        'timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,'
        'FOREIGN KEY(user_id) REFERENCES users(id)'
        ')'
    ),
    'config': (
        'CREATE TABLE IF NOT EXISTS config ('
        'key TEXT PRIMARY KEY, '
        'value TEXT'
        ')'
    )
}

def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:  # pragma: no cover - hard to trigger in tests
        raise RuntimeError(f"Datenbank konnte nicht geöffnet werden: {e}") from e
    conn.row_factory = sqlite3.Row
    return conn



def touch_refresh_flag() -> None:
    """Create or update the refresh flag file."""
    REFRESH_FLAG.parent.mkdir(exist_ok=True)
    REFRESH_FLAG.touch()


def set_exit_flag() -> None:
    """Create the exit flag file to signal termination."""
    EXIT_FLAG.parent.mkdir(exist_ok=True)
    EXIT_FLAG.touch()


def exit_flag_set() -> bool:
    """Return True if an exit has been requested."""
    return EXIT_FLAG.exists()


def clear_exit_flag() -> None:
    """Remove the exit flag file if it exists."""
    try:
        EXIT_FLAG.unlink()
    except FileNotFoundError:
        pass


def refresh_needed(last_mtime: float) -> bool:
    """Return True if the refresh flag has been modified since last_mtime."""
    if not REFRESH_FLAG.exists():
        return False
    return REFRESH_FLAG.stat().st_mtime > last_mtime


def upgrade_schema(conn: sqlite3.Connection) -> None:
    """Ensure all required columns and config entries exist."""
    cur = conn.execute("PRAGMA table_info(drinks)")
    cols = [row[1] for row in cur.fetchall()]
    if "min_stock" not in cols:
        conn.execute(
            "ALTER TABLE drinks ADD COLUMN min_stock INTEGER NOT NULL DEFAULT 0"
        )
    if "page" not in cols:
        conn.execute(
            "ALTER TABLE drinks ADD COLUMN page INTEGER NOT NULL DEFAULT 1"
        )

    cur = conn.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in cur.fetchall()]
    if "is_invoice" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_invoice INTEGER NOT NULL DEFAULT 0"
        )
    if "active" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
        )

    cur = conn.execute("SELECT COUNT(*) FROM config WHERE key='admin_pin'")
    if cur.fetchone()[0] == 0:
        conn.execute("INSERT INTO config(key, value) VALUES ('admin_pin', '1234')")
    conn.commit()



def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True
    cursor = conn.cursor()
    for stmt in _SCHEMA.values():
        cursor.execute(stmt)
    conn.commit()
    upgrade_schema(conn)
    cursor.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('overdraft_limit', '0')"
    )
    cursor.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('topup_uid', '')"
    )
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
    conn.execute("INSERT OR IGNORE INTO users (name, rfid_uid, balance) VALUES ('BARZAHLUNG', 'CASH', 0)")

    cur = conn.execute('SELECT COUNT(*) FROM drinks')
    if cur.fetchone()[0] == 0:
        conn.execute(
            'INSERT INTO drinks (name, price, stock, min_stock, page) VALUES (?, ?, ?, ?, ?)',
            ('Wasser', 150, 20, 5, 1))
        conn.execute(
            'INSERT INTO drinks (name, price, stock, min_stock, page) VALUES (?, ?, ?, ?, ?)',
            ('Cola', 200, 15, 5, 1))


    conn.commit()


def get_setting(key: str, conn: Optional[sqlite3.Connection] = None) -> str | None:
    own = False
    try:
        if conn is None:
            conn = get_connection()
            own = True
        cur = conn.execute('SELECT value FROM config WHERE key=?', (key,))
        row = cur.fetchone()
        return row['value'] if row else None
    finally:
        if own and conn is not None:
            conn.close()


def set_setting(key: str, value: str, conn: Optional[sqlite3.Connection] = None) -> None:
    own = False
    try:
        if conn is None:
            conn = get_connection()
            own = True
        conn.execute(
            'INSERT INTO config (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, value),
        )
        conn.commit()
    finally:
        if own and conn is not None:
            conn.close()
