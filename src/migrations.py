"""Migrations-Framework mit Auto-Backup + Rollback.

Ziel: Kein Datenverlust beim Update. Jede Migration ist eine reine
Python-Funktion, die eine ``sqlite3.Connection`` bekommt. Vor jeder
Migration wird automatisch ein Backup erzeugt und in
``schema_version(backup_path)`` protokolliert. Nach der Migration läuft
``PRAGMA integrity_check``; scheitert er, wird das Backup wiederhergestellt
und der Prozess bricht mit einem RuntimeError ab.

Rollback erfolgt bewusst nur manuell über ``restore_backup.sh``. Das
sichert dagegen, dass „hilfsbereite" Automatik im Fehlerfall doppelt Daten
zerstört.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import backups, database

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    note: str
    up: Callable[[sqlite3.Connection], None]


# ---------------------------------------------------------------------------
# Registrierte Migrationen. Neue Migrationen NUR unten anhängen, niemals
# in bestehende eingreifen – sonst geraten Bestandsdatenbanken durcheinander.
# ---------------------------------------------------------------------------


def _m002_wal_and_indexes(conn: sqlite3.Connection) -> None:
    """WAL-Modus + Basis-Indexe für schnelle Log-Abfragen."""
    # WAL kann nicht in einer Transaktion aktiviert werden.
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.isolation_level = ""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_timestamp ON transactions(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_drink ON transactions(drink_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_restocks_timestamp ON restocks(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_topups_timestamp ON topups(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_topups_user ON topups(user_id)")


def _m003_soft_delete(conn: sqlite3.Connection) -> None:
    """Soft-Delete-Spalten für Undo-Funktion (Getränke + Nutzer)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drinks)").fetchall()}
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE drinks ADD COLUMN deleted_at DATETIME")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN deleted_at DATETIME")


def _m004_categories(conn: sqlite3.Connection) -> None:
    """Getränke-Kategorien (optional, kein Zwang)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS drink_categories ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL UNIQUE, "
        "color TEXT, "
        "icon TEXT, "
        "sort_order INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drinks)").fetchall()}
    if "category_id" not in cols:
        conn.execute("ALTER TABLE drinks ADD COLUMN category_id INTEGER")


def _m005_price_history(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS price_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "drink_id INTEGER NOT NULL, "
        "old_price INTEGER, "
        "new_price INTEGER, "
        "changed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "changed_by TEXT"
        ")"
    )


def _m006_cash_closings(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cash_closings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "cashier TEXT, "
        "target_cents INTEGER, "
        "actual_cents INTEGER, "
        "diff_cents INTEGER, "
        "note TEXT"
        ")"
    )


def _m007_admins(conn: sqlite3.Connection) -> None:
    """Multi-Admin-Tabelle. Der bestehende Datei-basierte Admin bleibt Fallback."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS admins ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL UNIQUE, "
        "argon2_hash TEXT NOT NULL, "
        "role TEXT NOT NULL DEFAULT 'admin', "
        "totp_secret TEXT, "
        "rfid_uid TEXT UNIQUE, "
        "active INTEGER NOT NULL DEFAULT 1, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "last_login DATETIME"
        ")"
    )


def _m008_votes_and_webhooks(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS drink_wishes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, "
        "name TEXT NOT NULL, "
        "votes INTEGER NOT NULL DEFAULT 1, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wish_votes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "wish_id INTEGER NOT NULL, "
        "voter_uid TEXT NOT NULL, "
        "week TEXT NOT NULL, "
        "UNIQUE(voter_uid, week)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS webhooks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "url TEXT NOT NULL, "
        "events TEXT NOT NULL DEFAULT 'low_stock', "
        "active INTEGER NOT NULL DEFAULT 1"
        ")"
    )


def _m010_barcode(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drinks)").fetchall()}
    if "barcode" not in cols:
        conn.execute("ALTER TABLE drinks ADD COLUMN barcode TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drinks_barcode ON drinks(barcode)")


def _m011_ledger(conn: sqlite3.Connection) -> None:
    """Kassenbuch: Konten + Buchungen mit Beleg-Ablage."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS accounts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "code TEXT NOT NULL UNIQUE, "
        "name TEXT NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'cash', "     # cash|bank|income|expense|virtual
        "system INTEGER NOT NULL DEFAULT 0, "     # 1 = System-Konto (nicht löschbar)
        "sort_order INTEGER NOT NULL DEFAULT 100, "
        "note TEXT, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ledger_entries ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "kind TEXT NOT NULL, "                    # sale_cash|topup_cash|sale_card|transfer|expense|income|other|closing
        "from_account_id INTEGER, "               # NULL = außerhalb (Kunde, Lieferant)
        "to_account_id INTEGER, "                 # NULL = außerhalb
        "amount_cents INTEGER NOT NULL, "         # immer positiv, Vorzeichen ergibt sich aus from/to
        "ref TEXT, "                              # Verweis (z. B. Transaktion, Kauf)
        "actor TEXT, "                            # wer hat gebucht
        "note TEXT, "
        "receipt_path TEXT, "                     # relativer Pfad zu Beleg-Datei
        "reversed_of INTEGER, "                   # Storno-Zeiger
        "FOREIGN KEY(from_account_id) REFERENCES accounts(id), "
        "FOREIGN KEY(to_account_id) REFERENCES accounts(id)"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger_entries(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_from ON ledger_entries(from_account_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_to ON ledger_entries(to_account_id)")

    # System-Konten anlegen (falls noch nicht vorhanden).
    def _seed(code, name, kind, order, note):
        cur = conn.execute("SELECT id FROM accounts WHERE code=?", (code,)).fetchone()
        if not cur:
            conn.execute(
                "INSERT INTO accounts (code, name, kind, system, sort_order, note) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (code, name, kind, order, note),
            )

    _seed("terminal_cash", "Bargeld-Kasse am Terminal", "cash", 10,
          "Physisches Bargeld direkt im Automat / Kassenschale.")
    _seed("main_cash", "Hauptkasse (Kühlschrank)", "cash", 20,
          "Vereins-Bargeld – hierher wird die Terminal-Kasse geleert.")
    _seed("bank", "Bankkonto", "bank", 30, "Optional – Konto des Vereins.")
    _seed("income_drinks", "Erlöse Getränkeverkauf", "income", 40,
          "Sammelkonto für alle Verkäufe.")
    _seed("expenses_shopping", "Aufwand Einkauf", "expense", 50,
          "Wareneinkauf Getränke/Snacks.")
    _seed("expenses_other", "Aufwand Sonstiges", "expense", 60,
          "Reparaturen, Verbrauchsmaterial usw.")
    _seed("income_other", "Sonstige Einnahmen", "income", 70,
          "Spenden, Erstattungen usw.")


def _m012_closing_accounts(conn: sqlite3.Connection) -> None:
    """Kassensturz erweitern: pro Konto ausführbar."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cash_closings)").fetchall()}
    if "account_id" not in cols:
        conn.execute("ALTER TABLE cash_closings ADD COLUMN account_id INTEGER")
    if "ledger_entry_id" not in cols:
        conn.execute("ALTER TABLE cash_closings ADD COLUMN ledger_entry_id INTEGER")


def _m015_simplify_accounts(conn: sqlite3.Connection) -> None:
    """Reduziert das Konten-Modell auf: Kühlschrank + Hauptkasse + Sonst. Ein/Ausgaben.

    Alte Konten (Bank, Erlöse Getränkeverkauf, Aufwand Einkauf) bleiben in der
    DB erhalten (Historie!), werden aber als *hidden* markiert und aus den
    Auswahlen ausgeblendet. Namen der Kern-Konten werden angepasst.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "hidden" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")

    # Alte Konten ausblenden (nur wenn vorhanden).
    for code in ("bank", "income_drinks", "expenses_shopping"):
        conn.execute("UPDATE accounts SET hidden=1 WHERE code=?", (code,))

    # Umbenennen für klarere Sprache.
    conn.execute(
        "UPDATE accounts SET name=?, note=? WHERE code='terminal_cash'",
        ("Kühlschrank-Kasse", "Bargeld an der Getränkekasse (im Kühlschrank).")
    )
    conn.execute(
        "UPDATE accounts SET name=?, note=? WHERE code='main_cash'",
        ("Hauptkasse", "Sammelkasse des Vereins. Hier wird Bargeld gelagert und Einkäufe bezahlt.")
    )
    conn.execute(
        "UPDATE accounts SET name=?, note=? WHERE code='income_other'",
        ("Sonstige Einnahmen", "Spenden, Erstattungen, sonstige Einnahmen.")
    )
    conn.execute(
        "UPDATE accounts SET name=?, note=? WHERE code='expenses_other'",
        ("Sonstige Ausgaben", "Einkauf, Wartung, sonstige Ausgaben.")
    )


def _m014_recurring(conn: sqlite3.Connection) -> None:
    """Wiederkehrende Ausgaben (Buchungsvorlagen)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS recurring_expenses ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "amount_cents INTEGER NOT NULL, "
        "from_account_id INTEGER, "
        "to_account_id INTEGER, "
        "kind TEXT NOT NULL DEFAULT 'expense', "
        "interval TEXT NOT NULL DEFAULT 'monthly', "  # daily|weekly|monthly|yearly|manual
        "next_due DATE, "
        "last_posted DATETIME, "
        "active INTEGER NOT NULL DEFAULT 1, "
        "auto_post INTEGER NOT NULL DEFAULT 1, "
        "note TEXT, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
        ")"
    )


def _m013_receipt_ref(conn: sqlite3.Connection) -> None:
    """Kassenbon-Zuordnung: alle transactions eines Kaufs bekommen eine gemeinsame Ref."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    if "receipt_ref" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN receipt_ref TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_receipt ON transactions(receipt_ref)")


def _m009_activity_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS activity_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "kind TEXT NOT NULL, "
        "actor TEXT, "
        "target TEXT, "
        "amount_cents INTEGER, "
        "note TEXT"
        ")"
    )


MIGRATIONS: list[Migration] = [
    Migration(2, "WAL-Modus + Basis-Indexe", _m002_wal_and_indexes),
    Migration(3, "Soft-Delete für Getränke/Nutzer", _m003_soft_delete),
    Migration(4, "Getränke-Kategorien", _m004_categories),
    Migration(5, "Preishistorie", _m005_price_history),
    Migration(6, "Kassensturz-Tabelle", _m006_cash_closings),
    Migration(7, "Multi-Admin-Tabelle", _m007_admins),
    Migration(8, "Getränke-Wünsche + Webhooks", _m008_votes_and_webhooks),
    Migration(9, "Aktivitäts-Log", _m009_activity_log),
    Migration(10, "Barcode-Feld für Getränke", _m010_barcode),
    Migration(11, "Kassenbuch: Konten + Buchungen + Belege", _m011_ledger),
    Migration(12, "Kassensturz je Konto", _m012_closing_accounts),
    Migration(13, "Kassenbon-Referenz auf Transaktionen", _m013_receipt_ref),
    Migration(14, "Wiederkehrende Ausgaben (Vorlagen)", _m014_recurring),
    Migration(15, "Kontenmodell vereinfachen (Kühlschrank + Haupt + Sonst.)", _m015_simplify_accounts),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER NOT NULL, "
        "applied_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "note TEXT, "
        "backup_path TEXT"
        ")"
    )
    conn.commit()


def current_version(conn: sqlite3.Connection | None = None) -> int:
    own = False
    if conn is None:
        conn = database.get_connection()
        own = True
    try:
        _ensure_version_table(conn)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        version = int(row[0]) if row and row[0] is not None else 1
    finally:
        if own:
            conn.close()
    return version


def _apply_one(m: Migration, backup_path: Path | None) -> None:
    _LOG.info("migration %d start – %s", m.version, m.note)
    conn = database.get_connection()
    try:
        try:
            conn.execute("BEGIN")
            m.up(conn)
            conn.execute(
                "INSERT INTO schema_version (version, note, backup_path) VALUES (?, ?, ?)",
                (m.version, m.note, str(backup_path) if backup_path else None),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"integrity_check nach Migration {m.version}: {row}")
    finally:
        conn.close()
    _LOG.info("migration %d done", m.version)


def upgrade(*, allow_backup: bool = True) -> list[int]:
    """Führe alle ausstehenden Migrationen aus.

    * Legt vor der ersten anstehenden Migration ein Auto-Backup an (sofern
      Datenbank existiert und ``allow_backup`` gesetzt ist).
    * Bricht bei Fehler ab und restauriert das Backup.
    * Liefert die Liste der angewandten Versionen.
    """
    database.init_db()  # legt Basis-Tabellen (v1) an, sofern nötig
    conn = database.get_connection()
    _ensure_version_table(conn)
    cur_version = current_version(conn)
    if cur_version == 1 and not conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]:
        # Erstinstallation: v1 direkt eintragen.
        conn.execute(
            "INSERT INTO schema_version (version, note) VALUES (1, 'baseline')"
        )
        conn.commit()
    conn.close()

    pending = [m for m in MIGRATIONS if m.version > cur_version]
    if not pending:
        return []

    backup_path: Path | None = None
    if allow_backup and database.DB_PATH.exists():
        try:
            info = backups.create_backup()
            backup_path = info.path
            _LOG.info("pre-migration backup %s (%d bytes)", info.path, info.size)
        except FileNotFoundError:
            backup_path = None

    applied: list[int] = []
    for m in pending:
        try:
            _apply_one(m, backup_path)
            applied.append(m.version)
        except Exception as e:
            _LOG.error("migration %d failed: %s", m.version, e)
            if backup_path and backup_path.exists():
                _LOG.warning("rollback: restoring %s", backup_path)
                backups.restore_backup(backup_path)
            raise
    return applied


def status() -> dict:
    """Kurzer Status-Report für UI/API."""
    conn = database.get_connection()
    try:
        _ensure_version_table(conn)
        rows = conn.execute(
            "SELECT version, applied_at, note, backup_path FROM schema_version ORDER BY version"
        ).fetchall()
    finally:
        conn.close()
    current = int(rows[-1]["version"]) if rows else 1
    latest = max((m.version for m in MIGRATIONS), default=1)
    return {
        "current": current,
        "latest": latest,
        "pending": [
            {"version": m.version, "note": m.note}
            for m in MIGRATIONS if m.version > current
        ],
        "history": [dict(r) for r in rows],
        "up_to_date": current >= latest,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
