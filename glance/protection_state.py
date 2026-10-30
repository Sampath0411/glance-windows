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


def set_paused_until_wake(path: Path = STATE_PATH, paused: bool = True) -> bool:
    state = _read(path)
    state["paused_until_wake"] = bool(paused)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return False
    return True


def clear_pause_on_wake(path: Path = STATE_PATH) -> bool:
    return set_paused_until_wake(path, False)
