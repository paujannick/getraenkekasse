"""Multi-Admin-Konten mit Rollen, optionalem TOTP und RFID-Login.

Der klassische `admin`-Benutzer (Datei-basiertes Argon2-Passwort in
`data/admin_pw.txt`) bleibt als Fallback erhalten – z. B. wenn niemand
mehr an die DB kommt. Der Fallback-Admin hat immer die Rolle
``superadmin``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import struct
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from .database import get_connection

_HASHER = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)

ROLES = ("superadmin", "admin", "cashier", "restocker", "viewer")
ROLE_ORDER = {r: i for i, r in enumerate(ROLES)}


@dataclass
class Admin:
    id: int
    username: str
    role: str
    totp_enabled: bool
    rfid_uid: str | None
    active: int
    created_at: str
    last_login: str | None


def _row_to_admin(row: sqlite3.Row) -> Admin:
    return Admin(
        id=row["id"], username=row["username"], role=row["role"],
        totp_enabled=bool(row["totp_secret"]),
        rfid_uid=row["rfid_uid"], active=row["active"],
        created_at=row["created_at"], last_login=row["last_login"],
    )


def has_permission(role: str, minimum: str) -> bool:
    return ROLE_ORDER.get(role, -1) <= ROLE_ORDER.get(minimum, 99)


def list_admins() -> list[Admin]:
    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM admins ORDER BY username"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [_row_to_admin(r) for r in rows]


def get_by_username(username: str) -> Admin | None:
    with get_connection() as conn:
        try:
            row = conn.execute(
                "SELECT * FROM admins WHERE username=? AND active=1", (username,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _row_to_admin(row) if row else None


def get_by_uid(uid: str) -> Admin | None:
    if not uid:
        return None
    with get_connection() as conn:
        try:
            row = conn.execute(
                "SELECT * FROM admins WHERE rfid_uid=? AND active=1", (uid,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return _row_to_admin(row) if row else None


def create(username: str, password: str, role: str = "admin") -> Admin:
    username = username.strip()
    if not username:
        raise ValueError("Benutzername erforderlich")
    if role not in ROLES:
        raise ValueError(f"unbekannte Rolle {role!r}")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO admins (username, argon2_hash, role) VALUES (?, ?, ?)",
            (username, _HASHER.hash(password), role),
        )
        conn.commit()
    return get_by_username(username)  # type: ignore[return-value]


def verify(username: str, password: str) -> Admin | None:
    with get_connection() as conn:
        try:
            row = conn.execute(
                "SELECT * FROM admins WHERE username=? AND active=1", (username,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        try:
            _HASHER.verify(row["argon2_hash"], password)
        except (VerifyMismatchError, InvalidHash):
            return None
        conn.execute("UPDATE admins SET last_login=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        conn.commit()
    return _row_to_admin(row)


def set_password(admin_id: int, password: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE admins SET argon2_hash=? WHERE id=?",
                     (_HASHER.hash(password), int(admin_id)))
        conn.commit()


def set_role(admin_id: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unbekannte Rolle {role!r}")
    with get_connection() as conn:
        conn.execute("UPDATE admins SET role=? WHERE id=?", (role, int(admin_id)))
        conn.commit()


def set_active(admin_id: int, active: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE admins SET active=? WHERE id=?",
                     (1 if active else 0, int(admin_id)))
        conn.commit()


def bind_rfid(admin_id: int, uid: str | None) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE admins SET rfid_uid=? WHERE id=?",
                     ((uid or None), int(admin_id)))
        conn.commit()


def delete(admin_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM admins WHERE id=?", (int(admin_id),))
        conn.commit()


# ---------------------------------------------------------------------------
# TOTP (RFC 6238) – ohne Zusatz-Dependency
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_uri(username: str, secret: str, issuer: str = "Getränkekasse") -> str:
    from urllib.parse import quote
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(username)}"
        f"?secret={secret}&issuer={quote(issuer)}&period=30&digits=6&algorithm=SHA1"
    )


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (int.from_bytes(digest[off:off + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret_b32: str, when: float | None = None) -> str:
    ts = when if when is not None else time.time()
    return _hotp(secret_b32, int(ts // 30))


def verify_totp(secret_b32: str, code: str, tolerance: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = int(time.time() // 30)
    for delta in range(-tolerance, tolerance + 1):
        if _hotp(secret_b32, now + delta) == code:
            return True
    return False


def enable_totp(admin_id: int, secret: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE admins SET totp_secret=? WHERE id=?",
                     (secret, int(admin_id)))
        conn.commit()


def disable_totp(admin_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE admins SET totp_secret=NULL WHERE id=?", (int(admin_id),))
        conn.commit()


def get_totp_secret(admin_id: int) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT totp_secret FROM admins WHERE id=?", (int(admin_id),)
        ).fetchone()
    return row["totp_secret"] if row else None
