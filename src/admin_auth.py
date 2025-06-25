from __future__ import annotations

import hashlib
import os
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
    tmp_path = ADMIN_PW_FILE.with_suffix('.tmp')
    hashed = hashlib.sha256(password.encode()).hexdigest()
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        fh.write(hashed)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, ADMIN_PW_FILE)
