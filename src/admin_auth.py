from __future__ import annotations

import hashlib
from pathlib import Path

ADMIN_PW_FILE = Path(__file__).resolve().parent.parent / 'data' / 'admin_pw.txt'


def _default_hash() -> str:
    return hashlib.sha256('admin'.encode()).hexdigest()


def get_password_hash() -> str:
    if ADMIN_PW_FILE.exists():
        return ADMIN_PW_FILE.read_text().strip()
    return _default_hash()


def verify_password(password: str) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == get_password_hash()


def set_password(password: str) -> None:
    ADMIN_PW_FILE.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_PW_FILE.write_text(hashlib.sha256(password.encode()).hexdigest())
