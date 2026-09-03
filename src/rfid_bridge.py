"""RFID-Bridge: liefert Kartennummern per SSE an das Kiosk-Frontend.

Design-Prinzip: die Bridge ist **auf jedem System** lauffähig – auf einem Pi
mit MFRC522, auf Windows/Docker im Debug-Modus (Karten werden per
Simulator-Endpunkt eingespeist).

- Backend-Poller (Pi): eigener Thread liest MFRC522 kontinuierlich, drückt
  neue UIDs in eine interne Queue.
- Simulator (überall): `POST /api/kiosk/simulate` mit ``{"uid":"..."}``
  legt eine UID direkt in die Queue.
- SSE-Endpunkt `/events/rfid` streamt Karten an das Frontend
  (Keep-alive alle 15 s).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Iterable

_LOG = logging.getLogger(__name__)

_QUEUE: "queue.Queue[str]" = queue.Queue(maxsize=64)
_STARTED = False
_LOCK = threading.Lock()


def push_uid(uid: str) -> None:
    """Externe/interne Karten-Ereignisse in die Bridge einspeisen."""
    uid = (uid or "").strip()
    if not uid:
        return
    try:
        _QUEUE.put_nowait(uid)
    except queue.Full:
        _LOG.warning("rfid queue full – dropping uid %s", uid)


def _hardware_loop() -> None:
    try:
        from mfrc522 import MFRC522  # type: ignore
    except Exception:
        _LOG.info("no MFRC522 – hardware loop disabled (use simulator)")
        return
    try:
        reader = MFRC522()
    except Exception as e:  # pragma: no cover - Hardware fehlt
        _LOG.warning("MFRC522 init failed: %s", e)
        return

    last_uid = None
    last_seen = 0.0
    while True:
        try:
            status, _ = reader.MFRC522_Request(reader.PICC_REQIDL)
            if status == reader.MI_OK:
                status, uid_bytes = reader.MFRC522_Anticoll()
                if status == reader.MI_OK:
                    uid = "".join(f"{x:02X}" for x in uid_bytes[:4])
                    # Dedupe: dieselbe Karte innerhalb von 2 s nur einmal.
                    if uid != last_uid or (time.time() - last_seen) > 2.0:
                        push_uid(uid)
                        last_uid = uid
                        last_seen = time.time()
        except Exception as e:  # pragma: no cover
            _LOG.debug("mfrc522 loop: %s", e)
        time.sleep(0.1)


def start() -> None:
    """Startet den Hardware-Poller genau einmal (nur wenn Hardware da)."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        if os.environ.get("GK_DISABLE_RFID_HW") == "1":
            _LOG.info("rfid hardware disabled by env")
            return
        threading.Thread(target=_hardware_loop, daemon=True, name="rfid-hw").start()


def sse_stream() -> Iterable[bytes]:
    """Generator für den ``/events/rfid``-Endpunkt (Server-Sent Events)."""
    keepalive_every = 15.0
    last = time.time()
    while True:
        try:
            uid = _QUEUE.get(timeout=1.0)
            payload = json.dumps({"uid": uid, "ts": time.time()})
            yield f"event: rfid\ndata: {payload}\n\n".encode("utf-8")
            last = time.time()
        except queue.Empty:
            if time.time() - last >= keepalive_every:
                yield b": keepalive\n\n"
                last = time.time()
