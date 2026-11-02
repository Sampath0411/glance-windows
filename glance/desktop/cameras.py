"""Camera and display enumeration for the Windows desktop UI.

OpenCV index numbers alone cannot name devices, so cameras are enumerated
through the Windows PnP device database (PowerShell) with a numeric fallback
for devices that cannot be named. Displays come from EnumDisplayMonitors.

Both functions are intentionally tolerant: enumeration is best-effort and
never fails the UI.
"""

from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes

CREATE_NO_WINDOW = 0x08000000

_PNP_SCRIPT = (
    "Get-PnpDevice -Class Camera,Image -PresentOnly -ErrorAction SilentlyContinue "
    "| ForEach-Object { $_.FriendlyName }"
)


def enum_camera_names(timeout: float = 8.0) -> list[str]:
    """Return ordered camera friendly names, or [] when unavailable.

    PowerShell's PnP order is what OpenCV's index order usually follows, but
    there is no guarantee; the UI pairs names with indices as a hint and keeps
    a plain numeric fallback.
    """
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-Command", _PNP_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    names = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return names


# --- display enumeration via user32.EnumDisplayMonitors -------------------- #

_user32 = ctypes.WinDLL("user32")

_MONITORINFOF_PRIMARY = 1


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
    ctypes.POINTER(_RECT), wintypes.LPARAM,
)

_user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC, ctypes.POINTER(_RECT), _MONITORENUMPROC, wintypes.LPARAM,
]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL


def monitor_names() -> list[str]:
    """Return display labels: the primary monitor first, then externals.

    Returns ["Main display"] when only one monitor is present.
    """
    labels: list[str] = []
    primary: str | None = None

    def callback(_monitor, _dc, _rect, _data) -> int:
        nonlocal primary
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if _user32.GetMonitorInfoW(_monitor, ctypes.byref(info)):
            label = "Main display" if info.dwFlags & _MONITORINFOF_PRIMARY else "External display"
            if info.dwFlags & _MONITORINFOF_PRIMARY:
                primary = label
            else:
                labels.append(label)
        return 1

    try:
        _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(callback), 0)
    except Exception:  # noqa: BLE001 - enumeration must never break the UI
        return ["Main display"]
    result = []
    if primary is not None:
        result.append(primary)
    result.extend(labels)
    return result or ["Main display"]
