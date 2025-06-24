"""RFID reader abstraction.


Die UID wird ausschließlich per ``nfcpy`` von einem angeschlossenen Leser
eingelesen. Eine manuelle Eingabe in der GUI findet nicht mehr statt.

"""

from typing import Optional
from PyQt5 import QtWidgets

try:  # optional hardware support
    import nfc
except Exception:  # pragma: no cover - optional dependency
    nfc = None



def read_uid(timeout: int = 10, show_dialog: bool = True) -> Optional[str]:
    """Read a UID from the NFC reader.


    When ``show_dialog`` is True, a short message is shown on the screen
    prompting the user to place their card on the reader.
    """

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        created_app = True

    msg_box = None
    if show_dialog:
        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("RFID")
        msg_box.setText("Bitte Karte auflegen…")
        msg_box.setStandardButtons(QtWidgets.QMessageBox.NoButton)
        msg_box.show()
        app.processEvents()

    uid = None
    if nfc:
        try:
            with nfc.ContactlessFrontend('usb') as clf:
                tag = clf.connect(rdwr={'on-connect': lambda tag: False}, timeout=timeout)
                if tag and hasattr(tag, 'identifier'):
                    uid = tag.identifier.hex().upper()
        except Exception as exc:  # pragma: no cover - hardware errors
            print(f"RFID hardware error: {exc}")

    if msg_box:
        msg_box.close()

        app.processEvents()


    if created_app:
        app.quit()

    return uid


def read_uid_cli() -> Optional[str]:
    """Simple CLI UID input for the web interface."""
    try:
        return input("RFID UID eingeben: ").strip() or None
    except EOFError:
        return None

