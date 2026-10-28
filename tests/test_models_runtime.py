from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from glance import models


def make_archive(path: Path, entries: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_entries(license_bytes: bytes | None = b"non-commercial research") -> dict[str, bytes]:
    entries = {
        "buffalo_s/det_500m.onnx": b"detector",
        "buffalo_s/w600k_mbf.onnx": b"recognizer",
        "buffalo_s/genderage.onnx": b"not needed",
    }
    if license_bytes is not None:
        entries["buffalo_s/MODEL.LICENSE"] = license_bytes
    return entries


class ModelsPresentTests(unittest.TestCase):
    def test_requires_both_non_empty_graphs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertFalse(models.models_present(folder))
            (folder / "det_500m.onnx").write_bytes(b"")
            (folder / "w600k_mbf.onnx").write_bytes(b"x")
            self.assertFalse(models.models_present(folder))
            (folder / "det_500m.onnx").write_bytes(b"x")
            self.assertTrue(models.models_present(folder))
            self.assertEqual(models.missing_models(folder), ())

    def test_reports_missing_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(
                set(models.missing_models(temp)),
                {"det_500m.onnx", "w600k_mbf.onnx"},
            )


class ProvisionTests(unittest.TestCase):
    def test_installs_verified_archive_into_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "buffalo_s.zip"
            digest = make_archive(archive, official_entries())
            target = root / "models" / "buffalo_s"
            seen: list[float] = []

            def fake_download(destination: Path, *, progress=None) -> None:
                shutil.copyfile(archive, destination)
                if progress:
                    progress(0.5)
                    progress(1.0)

            with mock.patch.object(models, "MODEL_ARCHIVE_SHA256", digest), \
                    mock.patch.object(models, "EXPECTED_ARCHIVE_SIZE", archive.stat().st_size), \
                    mock.patch.object(models, "download_archive", fake_download):
                models.provision_models(target, progress=seen.append)

            self.assertTrue(models.models_present(target))
            self.assertFalse((target / "genderage.onnx").exists())
            self.assertEqual((target / "MODEL.LICENSE").read_bytes(), b"non-commercial research")
            self.assertEqual(seen[-1], 1.0)

    def test_second_run_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "buffalo_s"
            target.mkdir()
            for name in models.REQUIRED_MODELS:
                (target / name).write_bytes(b"existing")

            with mock.patch.object(models, "download_archive") as download:
                models.provision_models(target)

            download.assert_not_called()

    def test_failed_download_preserves_previous_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "buffalo_s"
            target.mkdir()
            (target / "det_500m.onnx").write_bytes(b"old detector")

            def broken(destination: Path, *, progress=None) -> None:
                raise RuntimeError("Could not reach GitHub to download the face models.")

            with mock.patch.object(models, "download_archive", broken):
                with self.assertRaisesRegex(RuntimeError, "Could not reach GitHub"):
                    models.provision_models(target)

            self.assertEqual((target / "det_500m.onnx").read_bytes(), b"old detector")
            self.assertFalse((target / "w600k_mbf.onnx").exists())

    def test_rejects_archive_without_required_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "buffalo_s.zip"
            digest = make_archive(archive, {"buffalo_s/det_500m.onnx": b"detector"})

            with mock.patch.object(models, "MODEL_ARCHIVE_SHA256", digest), \
                    mock.patch.object(models, "EXPECTED_ARCHIVE_SIZE", archive.stat().st_size), \
                    mock.patch.object(models, "download_archive",
                                      lambda destination, progress=None: shutil.copyfile(archive, destination)):
                with self.assertRaisesRegex(ValueError, "missing required files"):
                    models.provision_models(root / "models")

            self.assertFalse(models.models_present(root / "models"))

    def test_uses_bundled_notice_when_archive_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "buffalo_s.zip"
            digest = make_archive(archive, official_entries(license_bytes=None))

            with mock.patch.object(models, "MODEL_ARCHIVE_SHA256", digest), \
                    mock.patch.object(models, "EXPECTED_ARCHIVE_SIZE", archive.stat().st_size), \
                    mock.patch.object(models, "download_archive",
                                      lambda destination, progress=None: shutil.copyfile(archive, destination)):
                models.provision_models(root / "models")

            notice = (root / "models" / "MODEL.LICENSE").read_text(encoding="utf-8")
            self.assertIn("non-commercial", notice.lower())


class PackagingOmissionTests(unittest.TestCase):
    def test_build_script_supports_a_model_free_installer(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "packaging" / "build_windows.ps1")
        text = script.read_text(encoding="utf-8")
        self.assertIn("[switch]$WithoutModels", text)
        self.assertIn("licenses/MODEL.LICENSE", text)
        self.assertIn("A model-free build unexpectedly contains", text)


if __name__ == "__main__":
    unittest.main()
