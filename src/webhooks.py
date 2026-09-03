"""Webhooks für Discord/Slack – nur Ausgehend, keine E-Mails."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable

import requests

from .database import get_connection

_LOG = logging.getLogger(__name__)


def list_webhooks() -> list[dict]:
    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT id, name, url, events, active FROM webhooks ORDER BY id"
            ).fetchall()
        except Exception:
            return []
    return [dict(r) for r in rows]


def add_webhook(name: str, url: str, events: Iterable[str]) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO webhooks (name, url, events, active) VALUES (?, ?, ?, 1)",
            (name.strip() or "unnamed", url.strip(), ",".join(sorted(set(events)))),
        )
        conn.commit()
        return int(cur.lastrowid)


def delete_webhook(webhook_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM webhooks WHERE id=?", (int(webhook_id),))
        conn.commit()


def toggle_webhook(webhook_id: int, active: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE webhooks SET active=? WHERE id=?",
            (1 if active else 0, int(webhook_id)),
        )
        conn.commit()


def _post(url: str, payload: dict) -> None:
    try:
        if "discord.com" in url:
            body = {"content": payload.get("text") or json.dumps(payload)}
        elif "slack.com" in url or "hooks.slack" in url:
            body = {"text": payload.get("text") or json.dumps(payload)}
        else:
            body = payload
        requests.post(url, json=body, timeout=10)
    except requests.RequestException as e:
        _LOG.warning("webhook send failed: %s", e)


def dispatch(event: str, payload: dict) -> None:
    """Sende ``payload`` an alle Webhooks, die dieses ``event`` abonnieren.

    Läuft in einem Hintergrund-Thread – blockiert nie den Request.
    """
    hooks = [h for h in list_webhooks() if h["active"] and event in (h["events"] or "").split(",")]
    if not hooks:
        return

    def _worker() -> None:
        for h in hooks:
            _post(h["url"], payload)

    threading.Thread(target=_worker, daemon=True).start()
