"""Strukturiertes Logging mit Rotation (Console + Datei).

Bevorzugt JSON-Zeilen (parsebar für Loki/ELK), fällt auf klassisches
Textformat zurück, wenn ``GK_LOG_JSON=false`` gesetzt ist.
"""

from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "message", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName", "taskName",
            }:
                continue
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


def configure(level: str | None = None, use_json: bool | None = None) -> None:
    """Konfiguriere Root-Logger. Idempotent."""
    root = logging.getLogger()
    if getattr(root, "_gk_configured", False):
        return

    level_str = (level or os.environ.get("GK_LOG_LEVEL") or "INFO").upper()
    json_env = os.environ.get("GK_LOG_JSON", "true").lower() in {"1", "true", "yes"}
    is_json = json_env if use_json is None else use_json

    root.setLevel(getattr(logging, level_str, logging.INFO))

    formatter: logging.Formatter
    if is_json:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Werkzeug ist im Produktionsbetrieb (waitress) still, im Dev-Modus normal.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    root._gk_configured = True  # type: ignore[attr-defined]
