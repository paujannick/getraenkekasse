"""Passwort-Handling für den Admin-Bereich.

Ab v2.0 werden Passwörter mit Argon2id gehasht (via `argon2-cffi`).
Alte Installationen mit SHA-256-Hash in ``data/admin_pw.txt`` werden beim
ersten erfolgreichen Login transparent auf Argon2 migriert.

Die Standard-Zugangsdaten sind weiterhin ``admin/admin`` – nach dem ersten
Login sollte das Passwort zwingend geändert werden. Die Funktion
:func:`is_default_password` liefert dafür ein Flag.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

ADMIN_PW_FILE = Path(__file__).resolve().parent.parent / "data" / "admin_pw.txt"
DEFAULT_PASSWORD = "admin"

# Vernünftige Argon2id-Parameter für einen Raspberry Pi 4/5 (~50-100 ms).
_HASHER = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def _sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _is_legacy_sha(stored: str) -> bool:
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored.lower())


def _read_stored_hash() -> str:
    if not ADMIN_PW_FILE.exists():
        return ""
    try:
        return ADMIN_PW_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _atomic_write(text: str) -> None:
    ADMIN_PW_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ADMIN_PW_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, ADMIN_PW_FILE)
    try:
        # 0600 – nur für den Prozess-Owner lesbar (nur wirksam auf POSIX).
        os.chmod(ADMIN_PW_FILE, 0o600)
    except OSError:
        pass


def set_password(password: str) -> None:
    """Setze das Admin-Passwort (Argon2id, atomar geschrieben)."""
    if not password:
        raise ValueError("Passwort darf nicht leer sein")
    _atomic_write(_HASHER.hash(password))


def verify_password(password: str) -> bool:
    """Prüfe ``password``. Migriert bei Erfolg alte SHA-256-Hashes."""
    if not password:
        return False
    stored = _read_stored_hash()
    if not stored:
        # Erstes Setup – Default-Passwort akzeptieren und direkt hashen.
        if secrets.compare_digest(password, DEFAULT_PASSWORD):
            set_password(DEFAULT_PASSWORD)
            return True
        return False

    if _is_legacy_sha(stored):
        if secrets.compare_digest(_sha256(password), stored):
            # Migration auf Argon2.
            set_password(password)
            return True
        return False

    try:
        _HASHER.verify(stored, password)
    except (VerifyMismatchError, InvalidHash):
        return False

    if _HASHER.check_needs_rehash(stored):
        set_password(password)
    return True


def is_default_password() -> bool:
    """True, solange das Default-Passwort noch aktiv ist."""
    stored = _read_stored_hash()
    if not stored:
        return True
    if _is_legacy_sha(stored):
        return secrets.compare_digest(stored, _sha256(DEFAULT_PASSWORD))
    try:
        _HASHER.verify(stored, DEFAULT_PASSWORD)
    except (VerifyMismatchError, InvalidHash):
        return False
    return True
