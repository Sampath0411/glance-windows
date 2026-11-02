"""Small runtime flags shared between the desktop app and background agent."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import PATHS

STATE_PATH = PATHS.data_dir / "protection_state.json"


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def is_paused_until_wake(path: Path = STATE_PATH) -> bool:
    return _read(path).get("paused_until_wake") is True


def _write(state: dict, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return False
    return True


def set_paused_until_wake(path: Path = STATE_PATH, paused: bool = True) -> bool:
    state = _read(path)
    state["paused_until_wake"] = bool(paused)
    return _write(state, path)


def session_is_unlocked(path: Path = STATE_PATH, now: float | None = None) -> bool:
    """True while the settings session is unlocked and within its lifetime.

    The lifetime is the auto-lock session setting (7/14/30 days or Never);
    it is separate from idle auto-lock, which locks the PC itself.
    """
    until = _read(path).get("session_unlocked_until")
    if until is None:
        return False
    try:
        until = float(until)
    except (TypeError, ValueError):
        return False
    if until <= 0:
        return True  # Never expires
    return (now if now is not None else __import__("time").time()) < until


def set_session_unlocked(path: Path = STATE_PATH, until: float = 0.0) -> bool:
    state = _read(path)
    state["session_unlocked_until"] = float(until)
    return _write(state, path)


def lock_session(path: Path = STATE_PATH) -> bool:
    state = _read(path)
    state["session_unlocked_until"] = None
    return _write(state, path)


def clear_pause_on_wake(path: Path = STATE_PATH) -> bool:
    return set_paused_until_wake(path, False)
