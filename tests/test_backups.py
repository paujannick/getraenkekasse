from __future__ import annotations

from src import backups, database


def test_backup_and_restore_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "gk.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    database.init_db()

    dest_dir = tmp_path / "bk"
    monkeypatch.setenv("GK_BACKUP_DIR", str(dest_dir))

    info = backups.create_backup(dest_dir=dest_dir)
    assert info.path.exists()
    assert info.path.suffix == ".gz"
    assert len(info.sha256) == 64
    assert info.path.with_suffix(info.path.suffix + ".sha256").exists()

    # Datenbank absichtlich zerstören und wiederherstellen.
    db.write_bytes(b"not a sqlite file")
    backups.restore_backup(info.path)

    with database.get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    assert row[0] >= 1
