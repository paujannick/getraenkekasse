"""RFID reader utilities.

Die UID wird ausschließlich per ``nfcpy`` von einem angeschlossenen Leser
eingelesen. Eine manuelle Eingabe in der GUI findet nicht mehr statt. Dieses
Modul stellt Hilfsfunktionen bereit, um eine UID komfortabel über ein kleines
Dialogfenster einzulesen.
"""

from __future__ import annotations

from typing import Optional
from PyQt5 import QtWidgets
import threading
import time
import atexit

try:  # optional hardware support
    import nfc
    NFC_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    nfc = None
    NFC_AVAILABLE = False

_clf: 'nfc.ContactlessFrontend | None' = None


def _open_reader() -> 'nfc.ContactlessFrontend | None':
    """Open the NFC reader if possible and return the handle."""
    global _clf
    if not NFC_AVAILABLE:
        return None
    if _clf is None:
        try:
            _clf = nfc.ContactlessFrontend('usb')
        except Exception as exc:  # pragma: no cover - hardware errors
            print(f"RFID open error: {exc}")
            _clf = None
    return _clf


def _close_reader() -> None:
    """Close the reader if it is open."""
    global _clf
    if _clf is not None:
        try:
            _clf.close()
        finally:
            _clf = None

atexit.register(_close_reader)



def read_uid(timeout: int = 10, show_dialog: bool = True) -> Optional[str]:
    """Read a UID from the NFC reader.

    The function keeps the GUI responsive while waiting for the tag.  If no
    reader is available or an error occurs, ``None`` is returned instead of
    raising an exception.

    When ``show_dialog`` is ``True`` a small window is shown prompting the user
    to place their card on the reader.  The window is automatically closed once
    the UID was read or the timeout expired.
    """

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        created_app = True

    uid_box: dict[str, Optional[str]] = {"uid": None}
    error_box: dict[str, Optional[str]] = {"error": None}

    def worker() -> None:
        clf = _open_reader()
        if not clf:
            error_box["error"] = "unavailable"
            return
        try:
            tag = clf.connect(rdwr={"on-connect": lambda tag: False}, timeout=timeout)
            if tag and hasattr(tag, "identifier"):
                uid_box["uid"] = tag.identifier.hex().upper()
        except Exception as exc:  # pragma: no cover - hardware errors
            print(f"RFID hardware error: {exc}")
            error_box["error"] = str(exc)
            _close_reader()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    msg_box = None
    if show_dialog:
        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("RFID")
        if NFC_AVAILABLE:
            msg_box.setText("Bitte Karte auflegen…")
        else:
            msg_box.setText("Kein RFID-Leser verbunden")
        msg_box.setStandardButtons(QtWidgets.QMessageBox.NoButton)
        msg_box.show()

    start = time.time()
    while thread.is_alive() and time.time() - start < timeout:
        app.processEvents()
        time.sleep(0.05)

    thread.join(timeout=0)

    if msg_box:
        if error_box["error"] and NFC_AVAILABLE:
            msg_box.setText("Fehler beim Lesen der Karte")
            app.processEvents()
            time.sleep(1)
        msg_box.close()
        app.processEvents()

    if created_app:
        app.quit()

    return uid_box["uid"]


def read_uid_cli() -> Optional[str]:
    """Simple CLI UID input for the web interface."""
    try:
        return input("RFID UID eingeben: ").strip() or None
    except EOFError:
        return None

