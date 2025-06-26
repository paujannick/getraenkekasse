from __future__ import annotations

"""Control the NeoPixel strip for status feedback."""

import threading
import time
from typing import Tuple, Optional

try:
    import board  # type: ignore
    import neopixel  # type: ignore
except Exception:  # pragma: no cover - optional hardware
    board = None
    neopixel = None

# Allow overriding the pin via environment variable so other hardware can be
# connected without conflicts (e.g. the RFID reader uses SPI pins).
import os

_PIN_NAME = os.getenv("NEOPIXEL_PIN", "D12")
_PIN = getattr(board, _PIN_NAME, None) if board else None
_LED_COUNT = 8
_BRIGHTNESS = 0.2

_pixels = None
if neopixel and board and _PIN is not None:
    try:
        _pixels = neopixel.NeoPixel(
            _PIN, _LED_COUNT, brightness=_BRIGHTNESS, auto_write=False
        )
    except Exception as exc:  # pragma: no cover - optional hardware
        print(f"NeoPixel disabled: {exc}")
        _pixels = None

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _fade_once(color: Tuple[int, int, int], delay: float = 0.01) -> None:
    if not _pixels:
        return
    steps = list(range(0, 256, 8)) + list(range(255, -1, -8))
    for val in steps:
        scaled = tuple(int(val * c / 255) for c in color)
        _pixels.fill(scaled)
        _pixels.show()
        time.sleep(delay)


def _blink_loop(color: Tuple[int, int, int]) -> None:
    while not _stop_event.is_set():
        _fade_once(color)
    if _pixels:
        _pixels.fill((0, 0, 0))
        _pixels.show()


def _start_blink(color: Tuple[int, int, int]) -> None:
    global _thread
    stop()
    if not _pixels:
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_blink_loop, args=(color,), daemon=True)
    _thread.start()


def stop() -> None:
    global _thread
    if _thread and _thread.is_alive():
        _stop_event.set()
        _thread.join()
    _thread = None
    if _pixels:
        _pixels.fill((0, 0, 0))
        _pixels.show()


def flash(color: Tuple[int, int, int]) -> None:
    stop()
    _fade_once(color)
    if _pixels:
        _pixels.fill((0, 0, 0))
        _pixels.show()


# Convenience functions for the app

def indicate_waiting() -> None:
    """Blink blue while waiting for a card."""
    _start_blink((0, 0, 255))


def indicate_success() -> None:
    """Show green to indicate success."""
    flash((0, 255, 0))


def indicate_error() -> None:
    """Show red to indicate an error/unknown card."""
    flash((255, 0, 0))
