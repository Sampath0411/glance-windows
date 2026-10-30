"""Small, non-sensitive per-user state for one-time desktop notices."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..paths import PATHS

STATE_PATH = PATHS.data_dir / "ui_state.json"


def read_state(path: Path = STATE_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(state: dict, path: Path = STATE_PATH) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return False
    return True


def should_show_whats_new(version: str, path: Path = STATE_PATH) -> bool:
    return read_state(path).get("whats_new_version") != version


def mark_whats_new_shown(version: str, path: Path = STATE_PATH) -> bool:
    state = read_state(path)
    state["whats_new_version"] = version
    return write_state(state, path)


def should_offer_startup_prompt(path: Path = STATE_PATH) -> bool:
    return read_state(path).get("startup_prompt_dismissed") is not True


def dismiss_startup_prompt(path: Path = STATE_PATH) -> bool:
    state = read_state(path)
    state["startup_prompt_dismissed"] = True
    return write_state(state, path)
