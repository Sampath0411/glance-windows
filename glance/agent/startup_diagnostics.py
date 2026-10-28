"""Persist failures that occur before the agent's normal logger is initialized."""

from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable


def default_log_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Glance" / "agent-startup.log"


def _record(message: str, log_path: Path) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().astimezone().isoformat()}  {message}\n")
    except OSError:
        # Startup diagnostics must never prevent the agent from attempting to run.
        pass


def run_agent(entrypoint: Callable[[], int], *, log_path: Path | None = None) -> int:
    """Record pre-logger crashes and nonzero exits, while preserving the exit status."""
    destination = log_path or default_log_path()
    try:
        result = int(entrypoint())
    except BaseException:  # noqa: BLE001 - capture failures at the process boundary
        _record("Agent startup crashed:\n" + traceback.format_exc(), destination)
        return 1
    if result != 0:
        _record(f"Agent exited with code {result}.", destination)
    return result
