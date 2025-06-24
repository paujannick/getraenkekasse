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

try:  # optional hardware support
    import nfc
except Exception:  # pragma: no cover - optional dependency
    nfc = None



def read_uid(timeout: int = 10, show_dialog: bool = True) -> Optional[str]:
    """Read a UID from the NFC reader.

    This function now keeps the GUI responsive while waiting for the tag.

    When ``show_dialog`` is True a small window is shown prompting the user to
    place their card on the reader. The window is automatically closed once the
    UID was read or the timeout expired.
    """

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        created_app = True

    uid_box: dict[str, Optional[str]] = {"uid": None}

    def worker() -> None:
        if not nfc:
            return
        try:
            with nfc.ContactlessFrontend("usb") as clf:
                tag = clf.connect(rdwr={"on-connect": lambda tag: False}, timeout=timeout)
                if tag and hasattr(tag, "identifier"):
                    uid_box["uid"] = tag.identifier.hex().upper()
        except Exception as exc:  # pragma: no cover - hardware errors
            print(f"RFID hardware error: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    msg_box = None
    if show_dialog:
        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("RFID")
        msg_box.setText("Bitte Karte auflegen…")
        msg_box.setStandardButtons(QtWidgets.QMessageBox.NoButton)
        msg_box.show()

    start = time.time()
    while thread.is_alive() and time.time() - start < timeout:
        app.processEvents()
        time.sleep(0.05)

    thread.join(timeout=0)

    if msg_box:
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

