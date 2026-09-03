from __future__ import annotations

from src import admins, database, migrations


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "adm.db")
    monkeypatch.setenv("GK_BACKUP_DIR", str(tmp_path / "bk"))
    migrations.upgrade()


def test_create_and_verify(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    admins.create("alice", "geheimSecret1", role="admin")
    assert admins.verify("alice", "geheimSecret1") is not None
    assert admins.verify("alice", "falsch") is None


def test_role_change_and_permission(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = admins.create("bob", "geheimSecret1", role="viewer")
    assert admins.has_permission("viewer", "viewer")
    assert not admins.has_permission("viewer", "admin")
    admins.set_role(a.id, "admin")
    fresh = admins.get_by_username("bob")
    assert fresh and fresh.role == "admin"


def test_rfid_binding(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = admins.create("carol", "geheimSecret1")
    admins.bind_rfid(a.id, "ADMIN-001")
    assert admins.get_by_uid("ADMIN-001").username == "carol"
    admins.bind_rfid(a.id, None)
    assert admins.get_by_uid("ADMIN-001") is None


def test_totp_roundtrip(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = admins.create("dave", "geheimSecret1")
    secret = admins.generate_totp_secret()
    admins.enable_totp(a.id, secret)
    code = admins.totp_now(secret)
    assert admins.verify_totp(secret, code)
    assert not admins.verify_totp(secret, "000000")


def test_soft_deactivate(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = admins.create("eve", "geheimSecret1")
    admins.set_active(a.id, False)
    assert admins.verify("eve", "geheimSecret1") is None
