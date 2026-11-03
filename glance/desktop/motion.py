"""Small accessibility-aware motion preferences for the desktop UI."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

SPI_GETCLIENTAREAANIMATION = 0x1042


def motion_enabled(query: Callable[[], bool] | None = None) -> bool:
    """Return whether optional transitions are allowed; errors fail conservatively."""
    if query is not None:
        try:
            return bool(query())
        except Exception:  # noqa: BLE001 - accessibility API failure disables motion
            return False
    if sys.platform != "win32":
        return True
    try:
        enabled = ctypes.c_int()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
        return bool(ok and enabled.value)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
