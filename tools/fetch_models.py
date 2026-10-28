"""Fetch and safely stage the two InsightFace models used at runtime.

Thin CLI around :mod:`glance.models`, which the app also uses to provision
models on first run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glance import models as _models
from glance.models import (
    EXPECTED_ARCHIVE_SIZE,
    MODEL_ARCHIVE_SHA256,
    MODEL_URL,
    REQUIRED_MODELS,
    download_archive,
    stage_archive,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="fresh directory for the two model files")
    parser.add_argument("--archive", type=Path, help="use an already-downloaded archive")
    args = parser.parse_args()
    if args.archive:
        archive = args.archive
    else:
        archive = args.output.parent / "buffalo_s.zip"
        download_archive(archive)
    stage_archive(
        archive,
        args.output,
        expected_sha256=MODEL_ARCHIVE_SHA256,
        expected_size=EXPECTED_ARCHIVE_SIZE if not args.archive else None,
    )
    print(f"SHA-256 verified: {MODEL_ARCHIVE_SHA256}")
    print(f"Staged runtime models at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
