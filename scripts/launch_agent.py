"""Run the Glance agent from a source checkout.

Scheduled tasks do not let us set a working directory, so this wrapper puts the
repository root on ``sys.path`` before importing the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from glance.agent.glance_agent import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
