"""API-Token-Verwaltung (Argon2-Hash der Tokens)."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from ..database import get_connection

_HASHER = PasswordHasher(time_cost=2, memory_cost=32 * 1024, parallelism=2)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS api_tokens ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "name TEXT NOT NULL, "
    "prefix TEXT NOT NULL, "
    "hash TEXT NOT NULL, "
    "scopes TEXT NOT NULL DEFAULT 'read', "
    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
    "last_used DATETIME"
    ")"
)


@dataclass
class TokenRecord:
    id: int
    name: str
    prefix: str
    scopes: str
    created_at: str
    last_used: str | None


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    own = False
    if conn is None:
        conn = get_connection()
        own = True
    conn.execute(_SCHEMA)
    conn.commit()
    if own:
        conn.close()


def issue_token(name: str, scopes: str = "read") -> tuple[str, TokenRecord]:
    """Erzeuge ein neues Token. Der Klartext wird nur einmal zurückgegeben."""
    if scopes not in {"read", "write", "admin"}:
        scopes = "read"
    token = f"gkasse_{secrets.token_urlsafe(32)}"
    prefix = token[:14]
    with get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO api_tokens (name, prefix, hash, scopes) VALUES (?, ?, ?, ?)",
            (name.strip() or "unnamed", prefix, _HASHER.hash(token), scopes),
        )
        conn.commit()
        rec = TokenRecord(
            id=int(cur.lastrowid),
            name=name.strip() or "unnamed",
            prefix=prefix,
            scopes=scopes,
            created_at="",
            last_used=None,
        )
    return token, rec


def verify_token(token: str) -> TokenRecord | None:
    if not token:
        return None
    prefix = token[:14]
    with get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM api_tokens WHERE prefix=?", (prefix,)
        ).fetchall()
        for row in rows:
            try:
                _HASHER.verify(row["hash"], token)
            except (VerifyMismatchError, InvalidHash):
                continue
            conn.execute(
                "UPDATE api_tokens SET last_used=CURRENT_TIMESTAMP WHERE id=?", (row["id"],)
            )
            conn.commit()
            return TokenRecord(
                id=row["id"],
                name=row["name"],
                prefix=row["prefix"],
                scopes=row["scopes"],
                created_at=row["created_at"],
                last_used=row["last_used"],
            )
    return None


def list_tokens() -> list[TokenRecord]:
    with get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, name, prefix, scopes, created_at, last_used FROM api_tokens ORDER BY id"
        ).fetchall()
    return [
        TokenRecord(
            id=r["id"], name=r["name"], prefix=r["prefix"], scopes=r["scopes"],
            created_at=r["created_at"], last_used=r["last_used"],
        )
        for r in rows
    ]


def revoke(token_id: int) -> None:
    with get_connection() as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM api_tokens WHERE id=?", (int(token_id),))
        conn.commit()
