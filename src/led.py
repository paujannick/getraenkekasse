from __future__ import annotations

"""Serial based control for an external Arduino NeoPixel controller."""

from typing import Optional
import os

try:
    import serial  # type: ignore
except Exception as e:  # pragma: no cover - optional dependency
    serial = None  # type: ignore
    print(f"pyserial not available: {e}")


_PORT_CANDIDATES = ["/dev/ttyUSB0", "/dev/ttyACM0"]
_BAUDRATE = 9600

_serial: Optional["serial.Serial"] = None


def _init_serial() -> None:
    global _serial
    if serial is None:
        return
    for port in _PORT_CANDIDATES:
        if not os.path.exists(port):
            continue
        try:
            _serial = serial.Serial(port, _BAUDRATE, timeout=1)
            break
        except Exception as exc:  # pragma: no cover - hardware might be missing
            print(f"LED serial port {port} could not be opened: {exc}")
            _serial = None
    if _serial is None:
        print("LED controller not found - LEDs disabled")


_init_serial()


def _send(cmd: str) -> None:
    if _serial and _serial.is_open:
        try:
            _serial.write(cmd.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - serial failure
            print(f"Error sending to LED controller: {exc}")


def indicate_waiting() -> None:
    """Show waiting animation (blue chase)."""
    _send("card_read\n")


def indicate_success() -> None:
    """Turn strip green."""
    _send("success\n")


def indicate_error() -> None:
    """Turn strip red."""
    _send("error\n")


def off() -> None:
    """Switch off all LEDs."""
    _send("off\n")


def stop() -> None:
    """Alias for :func:`off`."""
    off()
