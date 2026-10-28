"""Nuitka entry point for the standalone lock-screen verification agent."""

import os

os.environ["GLANCE_PACKAGED"] = "1"

from glance.agent.startup_diagnostics import run_agent


def _run_agent() -> int:
    # Keep the application import inside the process-boundary handler so import-time
    # failures from native/runtime dependencies also reach the startup diagnostic.
    from glance.agent.glance_agent import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run_agent(_run_agent))
