"""RFID reader abstraction.

Versucht zuerst, die UID über ``nfcpy`` von einem angeschlossenen Leser zu
lesen. Ist kein Gerät vorhanden, kann die UID manuell eingegeben werden, sodass
die Anwendung auch ohne Hardware funktioniert.
"""

from typing import Optional
from PyQt5 import QtWidgets

try:  # optional hardware support
    import nfc
except Exception:  # pragma: no cover - optional dependency
    nfc = None


def read_uid(timeout: int = 10) -> Optional[str]:
    """Read a UID from a reader or fall back to manual input."""
    if nfc:
        try:
            with nfc.ContactlessFrontend('usb') as clf:
                tag = clf.connect(rdwr={'on-connect': lambda tag: False}, timeout=timeout)
                if tag and hasattr(tag, 'identifier'):
                    return tag.identifier.hex().upper()
        except Exception as exc:  # pragma: no cover - hardware errors
            print(f"RFID hardware error: {exc}")

    try:
        text, ok = QtWidgets.QInputDialog.getText(None, "RFID", "RFID UID eingeben:")
        if ok and text:
            return text.strip()
    except Exception:
        pass
    return None


def read_uid_cli() -> Optional[str]:
    """Simple CLI UID input for the web interface."""
    try:
        return input("RFID UID eingeben: ").strip() or None
    except EOFError:
        return None

