"""Test-Setup: stubbt Hardware- und Qt-Module, isoliert Datenbank pro Test."""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Stubs für Qt/RFID/LED, damit Importe auch ohne Hardware funktionieren.
_qtwidgets = types.SimpleNamespace(QMessageBox=object, QApplication=object)
_qtcore = types.SimpleNamespace(Qt=types.SimpleNamespace())
sys.modules.setdefault(
    "PyQt5", types.SimpleNamespace(QtWidgets=_qtwidgets, QtCore=_qtcore)
)
sys.modules.setdefault("PyQt5.QtWidgets", _qtwidgets)
sys.modules.setdefault("PyQt5.QtCore", _qtcore)

# Projekt auf sys.path.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
