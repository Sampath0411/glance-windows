from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools import fetch_models
from tools.fetch_models import stage_archive


class ModelStagingTests(unittest.TestCase):
    def make_archive(self, path: Path, entries: dict[str, bytes]) -> str:
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_stages_only_runtime_models_and_license(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "models.zip"
            digest = self.make_archive(archive, {
                "buffalo_s/det_500m.onnx": b"detector",
                "buffalo_s/w600k_mbf.onnx": b"recognizer",
                "buffalo_s/genderage.onnx": b"unused",
                "buffalo_s/MODEL.LICENSE": b"non-commercial research",
            })
            output = root / "staged"
            stage_archive(archive, output, expected_sha256=digest)
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"det_500m.onnx", "w600k_mbf.onnx", "MODEL.LICENSE"},
            )
            self.assertEqual((output / "MODEL.LICENSE").read_bytes(), b"non-commercial research")

    def test_rejects_digest_mismatch_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "models.zip"
            self.make_archive(archive, {"buffalo_s/det_500m.onnx": b"detector"})
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                stage_archive(archive, root / "staged", expected_sha256="0" * 64)
            self.assertFalse((root / "staged").exists())

    def test_rejects_traversal_entries_and_missing_models(self) -> None:
        for entries in (
            {"../det_500m.onnx": b"escape"},
            {
                "C:/temp/ignored.bin": b"absolute",
                "buffalo_s/det_500m.onnx": b"detector",
                "buffalo_s/w600k_mbf.onnx": b"recognizer",
            },
            {"buffalo_s/det_500m.onnx": b"detector"},
        ):
            with self.subTest(entries=list(entries)), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / "models.zip"
                digest = self.make_archive(archive, entries)
                with self.assertRaises(ValueError):
                    stage_archive(archive, root / "staged", expected_sha256=digest)
                self.assertFalse((root / "staged").exists())

    def test_accepts_official_flat_archive_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "models.zip"
            digest = self.make_archive(archive, {
                "det_500m.onnx": b"detector",
                "w600k_mbf.onnx": b"recognizer",
            })
            output = root / "staged"
            stage_archive(archive, output, expected_sha256=digest)
            self.assertTrue((output / "det_500m.onnx").is_file())
            self.assertTrue((output / "w600k_mbf.onnx").is_file())

    def test_cli_reports_pinned_digest_after_local_archive_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "models.zip"
            digest = self.make_archive(archive, {
                "det_500m.onnx": b"detector",
                "w600k_mbf.onnx": b"recognizer",
            })
            output = root / "staged"
            stdout = io.StringIO()
            with mock.patch.object(fetch_models, "MODEL_ARCHIVE_SHA256", digest), \
                    mock.patch.object(sys, "argv", ["fetch_models.py", str(output), "--archive", str(archive)]), \
                    redirect_stdout(stdout):
                self.assertEqual(fetch_models.main(), 0)
            self.assertIn(f"SHA-256 verified: {digest}", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
