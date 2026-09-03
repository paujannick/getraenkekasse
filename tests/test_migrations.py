from __future__ import annotations

import sqlite3

import pytest

from src import backups, database, migrations


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setenv("GK_BACKUP_DIR", str(tmp_path / "bk"))
    return database.DB_PATH


def test_upgrade_from_scratch_applies_all(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    applied = migrations.upgrade()
    assert set(applied) == {m.version for m in migrations.MIGRATIONS}
    # Zweiter Aufruf darf keine Änderungen mehr bringen.
    assert migrations.upgrade() == []


def test_status_reflects_state(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    migrations.upgrade()
    st = migrations.status()
    assert st["up_to_date"]
    assert st["current"] == st["latest"]
    assert st["pending"] == []


def test_pre_migration_backup_written(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    database.init_db()  # v1
    migrations.upgrade()
    files = list((tmp_path / "bk").glob("gkasse_*.db.gz"))
    assert files, "erwartete mindestens ein Auto-Backup"


def test_rollback_on_migration_failure(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    database.init_db()

    def bad_migration(conn: sqlite3.Connection) -> None:
        raise RuntimeError("boom")

    fake = migrations.Migration(999, "kaputt", bad_migration)
    original = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", original + [fake])

    with pytest.raises(RuntimeError):
        migrations.upgrade()

    # DB muss wieder auf einem konsistenten Stand sein.
    with database.get_connection() as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    assert row[0] == "ok"

    # Version darf 999 nicht enthalten.
    assert 999 not in {row["version"] for row in migrations.status()["history"]}


def test_barcode_column_exists_after_upgrade(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    migrations.upgrade()
    with database.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drinks)").fetchall()}
    assert "barcode" in cols
    assert "category_id" in cols
    assert "deleted_at" in cols
