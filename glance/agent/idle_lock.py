"""Input-based idle locking. No face inference or camera access."""
import ctypes
from ctypes import wintypes


def idle_seconds(config):
    if config.get("idle_lock_enabled") is not True:
        return 0
    try:
        minutes = int(config.get("idle_lock_minutes", 5))
    except (ValueError, TypeError, OverflowError):
        return 0
    return minutes * 60 if 1 <= minutes <= 120 else 0


class IdlePolicy:
    def __init__(self):
        self.last_locked_input = None

    def due(self, now, last_input, seconds, is_locked):
        return (seconds > 0 and not is_locked and
                last_input != self.last_locked_input and
                ((now - last_input) & 0xFFFFFFFF) >= seconds * 1000)

    def locked(self, last_input):
        self.last_locked_input = last_input


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def input_ticks():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF, int(info.dwTime)
