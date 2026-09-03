"""Interner Scheduler-Thread.

Läuft im Web-Admin-Prozess und übernimmt:

* Tägliches **Auto-Backup** (Default 03:00 Uhr, konfigurierbar).
* **Log-Rotation**: löscht ``logs/log_*.txt`` älter als N Tage.
* **Wiederkehrende Buchungen**: fällige Vorlagen automatisch buchen.

Idempotent gestartet über :func:`start`. Wenn ``GK_SCHEDULER_DISABLED=1``
gesetzt ist, wird nichts geplant (nützlich in Tests).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from . import backups, models, recurring

_LOG = logging.getLogger(__name__)
_STARTED = False
_LOCK = threading.Lock()

STATE: dict = {"last_backup_day": None, "last_run": None,
               "recurring_last": [], "log_purged": 0}


def _log_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "logs"


def _purge_old_logs(max_age_days: int = 30) -> int:
    cutoff = time.time() - max_age_days * 86400
    purged = 0
    ld = _log_dir()
    if not ld.exists():
        return 0
    for f in ld.glob("log_*.txt"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                purged += 1
        except OSError:
            pass
    return purged


def _tick() -> None:
    now = datetime.now()
    STATE["last_run"] = now.isoformat(timespec="seconds")

    # 1) Backup einmal pro Kalendertag ab konfigurierter Stunde.
    backup_hour = int(os.environ.get("GK_BACKUP_HOUR", "3"))
    today = now.date().isoformat()
    if STATE["last_backup_day"] != today and now.hour >= backup_hour:
        try:
            info = backups.create_backup()
            STATE["last_backup_day"] = today
            _LOG.info("auto backup written: %s (%d bytes)", info.path.name, info.size)
            # Optional: Kopie auf USB-Stick, wenn Pfad konfiguriert & gemountet
            try:
                usb = models.get_usb_backup_path()
            except Exception:
                usb = ""
            if usb:
                dest_dir = Path(usb)
                if dest_dir.exists() and dest_dir.is_dir():
                    try:
                        shutil.copy2(info.path, dest_dir / info.path.name)
                        sha = info.path.with_suffix(info.path.suffix + ".sha256")
                        if sha.exists():
                            shutil.copy2(sha, dest_dir / sha.name)
                        STATE["usb_last"] = today
                        _LOG.info("usb copy ok: %s", dest_dir)
                    except OSError as e:
                        STATE["usb_last_error"] = str(e)
                        _LOG.warning("usb copy failed: %s", e)
                else:
                    STATE["usb_last_error"] = f"path missing: {usb}"
        except FileNotFoundError:
            pass  # DB noch nicht angelegt
        except Exception as e:  # pragma: no cover
            _LOG.warning("auto backup failed: %s", e)

    # 2) Log-Rotation einmal pro Tag.
    if now.hour == backup_hour and now.minute < 5:
        purged = _purge_old_logs(int(os.environ.get("GK_LOG_RETENTION_DAYS", "30")))
        if purged:
            STATE["log_purged"] += purged
            _LOG.info("log rotation removed %d files", purged)

    # 3) Fällige wiederkehrende Buchungen anstoßen (immer, mit Duplikat-Schutz durch next_due).
    try:
        applied = recurring.run_due(now, actor="scheduler")
        if applied:
            STATE["recurring_last"] = applied
            _LOG.info("recurring auto-posted: %s", applied)
    except Exception as e:  # pragma: no cover
        _LOG.warning("recurring run failed: %s", e)


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as e:  # pragma: no cover
            _LOG.exception("scheduler tick failed: %s", e)
        # Alle 60 Sekunden prüfen – reicht für Tageslogik.
        time.sleep(60)


def start() -> None:
    """Idempotent: startet den Scheduler-Thread, wenn nicht deaktiviert."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        if os.environ.get("GK_SCHEDULER_DISABLED") == "1":
            _LOG.info("scheduler disabled by env")
            return
        _STARTED = True
        threading.Thread(target=_loop, daemon=True, name="gk-scheduler").start()
        _LOG.info("scheduler thread started")


def status() -> dict:
    return dict(STATE)
