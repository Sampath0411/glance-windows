"""Runtime compatibility checks for supported Windows PCs."""

from __future__ import annotations

import ctypes
import platform
import sys
from dataclasses import dataclass

MIN_WINDOWS_BUILD = 22000


@dataclass(frozen=True)
class CompatibilityResult:
    blockers: tuple[str, ...]

    @property
    def supported(self) -> bool:
        return not self.blockers


def evaluate_system(os_name: str, build: int, architecture: str,
                    pointer_bits: int = 64) -> CompatibilityResult:
    """Return hard compatibility blockers; setup tasks are not blockers."""
    blockers: list[str] = []
    if os_name.casefold() != "windows":
        blockers.append("Glance requires Windows 11 x64.")
    else:
        if build < MIN_WINDOWS_BUILD:
            blockers.append(
                f"Glance requires Windows 11 (build {MIN_WINDOWS_BUILD} or newer)."
            )
        if architecture.casefold() not in {"amd64", "x86_64", "x64"} or pointer_bits != 64:
            blockers.append("Glance requires a 64-bit x64 Windows system.")
    return CompatibilityResult(tuple(blockers))


def check_current_system() -> CompatibilityResult:
    """Read the running OS facts without touching the camera or user data."""
    if sys.platform != "win32":
        return evaluate_system(sys.platform, 0, platform.machine(),
                               ctypes.sizeof(ctypes.c_void_p) * 8)
    try:
        build = int(sys.getwindowsversion().build)
    except (AttributeError, OSError, TypeError, ValueError):
        build = 0
    return evaluate_system(
        "Windows", build, platform.machine(), ctypes.sizeof(ctypes.c_void_p) * 8
    )
