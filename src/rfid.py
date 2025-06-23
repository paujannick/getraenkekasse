"""RFID reader abstraction for testing.

In this test version there is no hardware dependency. Instead of reading from an
actual RFID reader the user is prompted to enter a UID manually. This allows the
application to run on macOS without additional hardware.
"""

from typing import Optional
from PyQt5 import QtWidgets


def read_uid(timeout: int = 10) -> Optional[str]:
    """Prompt the user for a UID and return it.

    In the real system this function would wait for a card on the ACR122U and
    return its UID. For testing we show a dialog asking for the UID.
    """
    try:
        text, ok = QtWidgets.QInputDialog.getText(
            None, "RFID Test", "RFID UID eingeben:")
        if ok and text:
            return text.strip()
        return None
    except Exception as exc:
        print(f"RFID read error: {exc}")
        return None



def read_uid_cli() -> Optional[str]:
    """Simple CLI UID input for the web interface."""
    try:
        return input("RFID UID eingeben: ").strip() or None
    except EOFError:
        return None

