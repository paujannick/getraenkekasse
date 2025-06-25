"""RFID reader utilities für MFRC522 mit GUI, ohne Neopixel."""

from __future__ import annotations
from typing import Optional
from mfrc522 import SimpleMFRC522
import time
from PyQt5 import QtWidgets, QtCore

def read_uid(timeout: int = 10, show_dialog: bool = True) -> Optional[str]:
    """Liest eine UID mit MFRC522, zeigt GUI an."""

    reader = SimpleMFRC522()

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
        msg_box.setWindowModality(QtCore.Qt.ApplicationModal)
        msg_box.setWindowFlags(msg_box.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        msg_box.show()

    start_time = time.time()
    uid_hex: Optional[str] = None

    try:
        print("Bitte Karte auflegen...")
        while time.time() - start_time < timeout:
            app.processEvents()
            id, text = reader.read_no_block()
            if id:
                uid_hex = format(id, 'X').upper()
                print(f"Gelesene UID: {uid_hex}")
                time.sleep(1)
                break
            time.sleep(0.1)

        if uid_hex is None:
            print("Timeout: Keine Karte gelesen.")
    except Exception as e:
        print(f"Fehler beim Lesen: {e}")
    finally:
        if msg_box:
            msg_box.close()
            app.processEvents()
        if created_app:
            app.quit()

    return uid_hex

# Dummy-Test
if __name__ == "__main__":
    uid = read_uid(timeout=10, show_dialog=True)
    if uid:
        print(f"UID erfolgreich gelesen: {uid}")
    else:
        print("Keine UID gelesen.")
