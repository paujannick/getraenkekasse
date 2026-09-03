"""Zentrale Sicherheits-Helfer: SECRET_KEY, Session-Härtung, sichere Uploads.

Alle Funktionen sind idempotent und in Tests deterministisch nutzbar.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import uuid
from collections.abc import Iterable
from pathlib import Path

from werkzeug.utils import secure_filename

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SECRET_KEY_FILE = DATA_DIR / "flask_secret.key"

# 5 MiB Upload-Standardlimit (Bilder, QR-Codes).
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def get_or_create_secret_key() -> bytes:
    """Liefert einen persistenten, zufälligen Flask-SECRET_KEY.

    * Wenn ``FLASK_SECRET_KEY`` in der Umgebung gesetzt ist, wird der
      Wert direkt verwendet (empfohlen für Container-Deployments).
    * Andernfalls wird beim ersten Aufruf ein 64-Byte-Zufallswert
      erzeugt und in ``data/flask_secret.key`` mit 0600 abgelegt.
    """
    env = os.environ.get("FLASK_SECRET_KEY")
    if env:
        return env.encode("utf-8")
    if SECRET_KEY_FILE.exists():
        try:
            data = SECRET_KEY_FILE.read_bytes().strip()
            if len(data) >= 32:
                return data
        except OSError:
            pass
    key = secrets.token_bytes(64)
    _atomic_write_bytes(SECRET_KEY_FILE, key)
    return key


def apply_session_hardening(app) -> None:
    """Setze sichere Cookie- und Session-Defaults auf einer Flask-App."""
    session_secure = os.environ.get("GK_SESSION_SECURE", "false").lower() in {"1", "true", "yes"}
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=session_secure,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,  # 8 Stunden
        MAX_CONTENT_LENGTH=int(os.environ.get("GK_MAX_UPLOAD_MB", "5")) * 1024 * 1024,
    )


def add_security_headers(response):
    """Setze konservative Security-Header. Für Flask ``after_request``."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self' https://api.open-meteo.com; "
        "frame-ancestors 'self'; base-uri 'self'",
    )
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


def safe_upload_name(original: str, allowed_exts: Iterable[str] = ALLOWED_IMAGE_EXTS) -> str | None:
    """Sanitisiere Uploads. Liefert ``None`` bei ungültigem Typ.

    Erzeugt einen zufälligen Dateinamen mit der (validierten) Original-Endung,
    verhindert damit Path-Traversal, Kollisionen und das Überschreiben von
    bestehenden Dateien.
    """
    if not original:
        return None
    clean = secure_filename(original)
    if not clean:
        return None
    ext = Path(clean).suffix.lower()
    allowed = {e.lower() for e in allowed_exts}
    if ext not in allowed:
        return None
    return f"{uuid.uuid4().hex}{ext}"
