"""RFID UID Reader für MFRC522 — saubere UID-Abfrage ohne AUTH ERRORs."""

from __future__ import annotations
from typing import Optional
import time
from PyQt5 import QtWidgets, QtCore

from mfrc522 import MFRC522
import RPi.GPIO as GPIO
from . import led


try:
    from mfrc522 import MFRC522
    import RPi.GPIO as GPIO
    GPIO.setwarnings(False)
except Exception as e:  # pragma: no cover - hardware might be missing
    MFRC522 = None  # type: ignore
    GPIO = None  # type: ignore
    print(f"RFID-Initialisierung fehlgeschlagen: {e}")

def read_uid(timeout: int = 10, show_dialog: bool = True) -> Optional[str]:
    """Liest nur die UID mit MFRC522, zeigt GUI an, keine AUTH ERRORs mehr."""

    if MFRC522 is None:
        print("RFID-Reader nicht verfügbar")
        if show_dialog:
            QtWidgets.QMessageBox.warning(None, "RFID", "RFID-Reader nicht verfügbar")
        return None

    reader = MFRC522()
    led.indicate_waiting()

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
        font = msg_box.font()
        font.setPointSize(20)
        msg_box.setFont(font)
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

            (status, tag_type) = reader.MFRC522_Request(reader.PICC_REQIDL)

            if status == reader.MI_OK:
                (status, uid) = reader.MFRC522_Anticoll()
                if status == reader.MI_OK:
                    uid_hex = ''.join(f"{x:02X}" for x in uid)
                    print(f"Gelesene UID: {uid_hex}")
                    time.sleep(1)
                    break

            time.sleep(0.1)

        if uid_hex is None:
            print("Timeout: Keine Karte gelesen.")
            led.stop()
    except Exception as e:
        print(f"Fehler beim Lesen: {e}")
        led.stop()
    finally:
        if msg_box:
            msg_box.close()
            app.processEvents()
        if created_app:
            app.quit()

        GPIO.cleanup()
        led.stop()


    return uid_hex

# Dummy-Test
if __name__ == "__main__":
    uid = read_uid(timeout=10, show_dialog=True)
    if uid:
        print(f"UID erfolgreich gelesen: {uid}")
    else:
        print("Keine UID gelesen.")
