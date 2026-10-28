"""Provision the InsightFace models used at runtime.

The model weights are not redistributed with Glance. They are downloaded from
the official InsightFace release on the user's behalf, checksum-verified, and
staged outside the installation directory.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

MODEL_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip"
MODEL_TERMS_URL = "https://github.com/deepinsight/insightface/tree/master/model_zoo"
EXPECTED_ARCHIVE_SIZE = 127_607_557
MODEL_ARCHIVE_SHA256 = "d85a87f503f691807cd8bb97128bdf7a0660326cd9cd02657127fa978bab8b5e"
UNCOMPRESSED_MODEL_BYTES = 2524817 + 13616099
REQUIRED_MODELS = ("det_500m.onnx", "w600k_mbf.onnx")

Progress = Callable[[float], None]


def models_present(directory: Path | str) -> bool:
    """True when both runtime graphs exist and are non-empty."""
    folder = Path(directory)
    try:
        return all((folder / name).is_file() and (folder / name).stat().st_size > 0
                   for name in REQUIRED_MODELS)
    except OSError:
        return False


def missing_models(directory: Path | str) -> tuple[str, ...]:
    folder = Path(directory)
    return tuple(name for name in REQUIRED_MODELS if not (folder / name).is_file())


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


def bundled_license() -> Path | None:
    """Locate the shipped copy of the upstream model notice."""
    root = Path(__file__).resolve().parent
    install_root = Path(sys.executable).resolve().parent
    for candidate in (root.parent / "packaging" / "MODEL.LICENSE",
                      install_root / "licenses" / "MODEL.LICENSE",
                      root / "licenses" / "MODEL.LICENSE"):
        if candidate.is_file():
            return candidate
    return None


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
                notice = bundled_license()
                if notice is None:
                    raise ValueError("the upstream buffalo_s MODEL.LICENSE notice is missing")
                shutil.copyfile(notice, staging / "MODEL.LICENSE")
            staging.replace(output_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def download_archive(destination: Path, *, progress: Progress | None = None) -> None:
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
    reported = -1
    try:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Glance-model-fetch/1"})
        try:
            response = urllib.request.urlopen(request, timeout=120)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError(
                "Could not reach GitHub to download the face models. Check the connection "
                "and try again."
            ) from error
        with response, temporary.open("wb") as target:
            while block := response.read(1024 * 1024):
                size += len(block)
                digest.update(block)
                target.write(block)
                if progress:
                    percent = int(size * 1000 / EXPECTED_ARCHIVE_SIZE)
                    if percent != reported:
                        reported = percent
                        progress(min(1.0, size / EXPECTED_ARCHIVE_SIZE))
        if size != EXPECTED_ARCHIVE_SIZE:
            raise ValueError(f"unexpected model archive size: {size}")
        actual = digest.hexdigest()
        if actual.lower() != MODEL_ARCHIVE_SHA256.lower():
            raise ValueError(f"model archive SHA-256 mismatch: {actual}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def provision_models(target_dir: Path | str, *, progress: Progress | None = None) -> Path:
    """Download, verify, and install the runtime models into ``target_dir``.

    Existing complete models are kept. Files are replaced individually, so a
    failed download or a corrupt archive never damages a previous install.
    """
    target = Path(target_dir)
    if models_present(target):
        if progress:
            progress(1.0)
        return target

    with tempfile.TemporaryDirectory(prefix="glance-models-") as temporary:
        archive = Path(temporary) / "buffalo_s.zip"
        download_archive(archive, progress=lambda value: progress(value * 0.85) if progress else None)
        staged = Path(temporary) / "staged"
        stage_archive(
            archive,
            staged,
            expected_sha256=MODEL_ARCHIVE_SHA256,
            expected_size=EXPECTED_ARCHIVE_SIZE,
        )
        if not models_present(staged):
            raise ValueError("the downloaded archive did not contain the required models")
        target.mkdir(parents=True, exist_ok=True)
        for name in (*REQUIRED_MODELS, "MODEL.LICENSE"):
            source = staged / name
            if source.is_file():
                os.replace(source, target / name)
        if progress:
            progress(1.0)

    if not models_present(target):
        raise ValueError("the face models were not installed correctly")
    return target
