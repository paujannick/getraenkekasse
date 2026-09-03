"""Modernes Backup-Modul.

Nutzt die native SQLite-Backup-API (``VACUUM INTO``) für einen konsistenten
Snapshot, komprimiert mit gzip und schreibt eine SHA-256-Checksum. Rotation
nach Anzahl und maximalem Alter (Tagen).

Optionaler Upload auf einen WebDAV-Endpunkt (z. B. Nextcloud) via einfachem
HTTP PUT – ohne zusätzliche Dependencies.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import database

_LOG = logging.getLogger(__name__)


def _db_path() -> Path:
    return database.DB_PATH


def _default_dir() -> Path:
    return _db_path().parent / "backups"
DEFAULT_KEEP = 20
DEFAULT_MAX_AGE_DAYS = 90

# Rückwärtskompatible Attribut-Namen (falls externer Code darauf zugriff hat).
DB_PATH = None  # deprecated, siehe database.DB_PATH
DEFAULT_DIR = None


@dataclass
class BackupInfo:
    path: Path
    sha256: str
    size: int


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(
    dest_dir: Path | None = None,
    *,
    keep: int = DEFAULT_KEEP,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> BackupInfo:
    """Erzeuge einen konsistenten, gzippten SQLite-Snapshot mit Checksum."""
    db_path = _db_path()
    if not db_path.exists():
        raise FileNotFoundError("Datenbank existiert noch nicht")

    dest = Path(os.environ.get("GK_BACKUP_DIR") or dest_dir or _default_dir())
    dest.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    raw = dest / f"gkasse_{ts}.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM INTO ?", (str(raw),))
    finally:
        conn.close()

    gz_path = raw.with_suffix(".db.gz")
    with open(raw, "rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
        for chunk in iter(lambda: src.read(64 * 1024), b""):
            dst.write(chunk)
    raw.unlink(missing_ok=True)

    digest = _sha256_file(gz_path)
    (gz_path.with_suffix(gz_path.suffix + ".sha256")).write_text(
        f"{digest}  {gz_path.name}\n", encoding="utf-8"
    )

    _prune(dest, keep=keep, max_age_days=max_age_days)

    info = BackupInfo(path=gz_path, sha256=digest, size=gz_path.stat().st_size)
    _LOG.info("backup created", extra={"path": str(info.path), "sha256": digest, "bytes": info.size})
    return info


def _prune(directory: Path, *, keep: int, max_age_days: int) -> None:
    files = sorted(
        directory.glob("gkasse_*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    to_delete: list[Path] = []
    if len(files) > keep:
        to_delete.extend(files[keep:])
    cutoff = time.time() - max_age_days * 86400
    for f in files:
        if f.stat().st_mtime < cutoff and f not in to_delete:
            to_delete.append(f)
    for f in to_delete:
        try:
            f.unlink()
            f.with_suffix(f.suffix + ".sha256").unlink(missing_ok=True)
        except OSError:
            pass


def restore_backup(archive: Path) -> None:
    """Stelle eine Backup-Datei wieder her (unterstützt .db und .db.gz)."""
    if not archive.exists():
        raise FileNotFoundError(archive)

    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".restore.tmp")

    if archive.suffix == ".gz":
        with gzip.open(archive, "rb") as src, open(tmp, "wb") as dst:
            for chunk in iter(lambda: src.read(64 * 1024), b""):
                dst.write(chunk)
    else:
        import shutil

        shutil.copyfile(archive, tmp)

    # Kurze Integritätsprüfung.
    conn = sqlite3.connect(tmp)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"Backup korrupt: {row}")
    finally:
        conn.close()

    os.replace(tmp, db_path)


def upload_webdav(info: BackupInfo) -> bool:
    """Optionaler Upload auf einen WebDAV-Server (Nextcloud etc.)."""
    url = os.environ.get("GK_BACKUP_WEBDAV_URL") or ""
    if not url:
        return False
    user = os.environ.get("GK_BACKUP_WEBDAV_USER") or ""
    pw = os.environ.get("GK_BACKUP_WEBDAV_PASS") or ""
    target = url.rstrip("/") + "/" + info.path.name
    try:
        with open(info.path, "rb") as fh:
            resp = requests.put(target, data=fh, auth=(user, pw), timeout=60)
        resp.raise_for_status()
        _LOG.info("backup uploaded", extra={"url": target, "sha256": info.sha256})
        return True
    except requests.RequestException as e:
        _LOG.warning("backup upload failed", extra={"error": str(e), "url": target})
        return False
