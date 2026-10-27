"""Nuitka entry point for the packaged Glance GUI."""

import os

os.environ["GLANCE_PACKAGED"] = "1"

from desktop.main import main


if __name__ == "__main__":
    raise SystemExit(main())
