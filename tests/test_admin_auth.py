from __future__ import annotations

import hashlib

from src import admin_auth


def test_default_password_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_PW_FILE", tmp_path / "pw.txt")
    assert admin_auth.is_default_password()
    assert admin_auth.verify_password("admin")
    assert not admin_auth.verify_password("wrong")


def test_legacy_sha256_is_migrated(tmp_path, monkeypatch):
    file = tmp_path / "pw.txt"
    file.write_text(hashlib.sha256(b"secret").hexdigest(), encoding="utf-8")
    monkeypatch.setattr(admin_auth, "ADMIN_PW_FILE", file)

    assert admin_auth.verify_password("secret")
    stored = file.read_text(encoding="utf-8").strip()
    assert stored.startswith("$argon2"), "legacy hash should be migrated to argon2"


def test_change_password(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_PW_FILE", tmp_path / "pw.txt")
    admin_auth.set_password("neuGenug1")
    assert admin_auth.verify_password("neuGenug1")
    assert not admin_auth.verify_password("neuGenug2")
    assert not admin_auth.is_default_password()


def test_default_password_flag_reflects_current_state(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_PW_FILE", tmp_path / "pw.txt")
    admin_auth.set_password("admin")
    assert admin_auth.is_default_password()
    admin_auth.set_password("someOther")
    assert not admin_auth.is_default_password()
