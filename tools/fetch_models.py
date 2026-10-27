"""Fetch and safely stage the two InsightFace models used at runtime."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

MODEL_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip"
EXPECTED_ARCHIVE_SIZE = 127_607_557
MODEL_ARCHIVE_SHA256 = "d85a87f503f691807cd8bb97128bdf7a0660326cd9cd02657127fa978bab8b5e"
REQUIRED_MODELS = ("det_500m.onnx", "w600k_mbf.onnx")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_name(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    windows_member = PureWindowsPath(name)
    if (member.is_absolute() or windows_member.is_absolute() or windows_member.drive
            or ".." in member.parts or "\\" in name):
        raise ValueError(f"unsafe archive entry: {name!r}")
    return member


def stage_archive(
    archive_path: Path,
    output_dir: Path,
    *,
    expected_sha256: str,
    expected_size: int | None = None,
) -> None:
    """Validate an archive completely, then atomically stage required files only."""
    archive_path = Path(archive_path)
    output_dir = Path(output_dir)
    if not expected_sha256 or len(expected_sha256) != 64:
        raise ValueError("a pinned 64-character SHA-256 is required")
    if output_dir.exists():
        raise FileExistsError(f"model staging directory already exists: {output_dir}")
    if expected_size is not None and archive_path.stat().st_size != expected_size:
        raise ValueError("model archive size does not match the pinned size")
    actual = _sha256(archive_path)
    if actual.lower() != expected_sha256.lower():
        raise ValueError(f"model archive SHA-256 mismatch: {actual}")

    required = set(REQUIRED_MODELS)
    selected: dict[str, zipfile.ZipInfo] = {}
    license_member: zipfile.ZipInfo | None = None
    with zipfile.ZipFile(archive_path) as bundle:
        for info in bundle.infolist():
            member = _safe_member_name(info.filename)
            if info.is_dir():
                continue
            if member.name in required and member.parts in (
                (member.name,), ("buffalo_s", member.name)
            ):
                if member.name in selected:
                    raise ValueError(f"duplicate model archive entry: {member.name}")
                selected[member.name] = info
            elif member.parts in (("MODEL.LICENSE",), ("buffalo_s", "MODEL.LICENSE")):
                license_member = info
        missing = required - selected.keys()
        if missing:
            raise ValueError(f"model archive is missing required files: {', '.join(sorted(missing))}")

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="models-", dir=output_dir.parent))
        try:
            for name, info in selected.items():
                target = staging / name
                with bundle.open(info) as source, target.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
            if license_member is not None:
                (staging / "MODEL.LICENSE").write_bytes(bundle.read(license_member))
            else:
                license_source = Path(__file__).resolve().parents[1] / "packaging" / "MODEL.LICENSE"
                if not license_source.is_file():
                    raise ValueError("the upstream buffalo_s MODEL.LICENSE notice is missing")
                shutil.copyfile(license_source, staging / "MODEL.LICENSE")
            staging.replace(output_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def download_archive(destination: Path) -> None:
    """Stream the pinned upstream archive to a temporary sibling, then replace."""
    if not MODEL_ARCHIVE_SHA256:
        raise RuntimeError("official model archive SHA-256 has not been pinned")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="buffalo_s-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Glance-model-fetch/1"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as target:
            while block := response.read(1024 * 1024):
                size += len(block)
                digest.update(block)
                target.write(block)
        if size != EXPECTED_ARCHIVE_SIZE:
            raise ValueError(f"unexpected model archive size: {size}")
        actual = digest.hexdigest()
        if actual.lower() != MODEL_ARCHIVE_SHA256.lower():
            raise ValueError(f"model archive SHA-256 mismatch: {actual}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


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
