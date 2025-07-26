from __future__ import annotations

import bcrypt

import os

from pathlib import Path

ADMIN_PW_FILE = Path(__file__).resolve().parent.parent / 'data' / 'admin_pw.txt'

DEFAULT_HASH = bcrypt.hashpw(b'admin', bcrypt.gensalt())


def _default_hash() -> str:
    return DEFAULT_HASH.decode()


def get_password_hash() -> str:
    if ADMIN_PW_FILE.exists():
        return ADMIN_PW_FILE.read_text().strip()
    return _default_hash()


def verify_password(password: str) -> bool:
    hashed = get_password_hash().encode()
    return bcrypt.checkpw(password.encode(), hashed)


def set_password(password: str) -> None:
    ADMIN_PW_FILE.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = ADMIN_PW_FILE.with_suffix('.tmp')
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        fh.write(hashed)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, ADMIN_PW_FILE)

