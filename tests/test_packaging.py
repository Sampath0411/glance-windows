"""Source and packaging policy regression tests."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
import hashlib
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryPolicyTests(unittest.TestCase):
    def test_gitignore_excludes_local_data_models_and_build_outputs(self) -> None:
        ignore_file = ROOT / ".gitignore"
        self.assertTrue(ignore_file.is_file(), "repository .gitignore is missing")
        rules = [
            line.strip()
            for line in ignore_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        required = (
            ".venv/", "data/", "models/", "__pycache__/", "dist/", ".build/",
            "/RECOVERY.txt",
        )
        for pattern in required:
            self.assertTrue(
                any(rule == pattern or rule.endswith("/" + pattern) for rule in rules),
                f".gitignore does not exclude {pattern}",
            )

    def test_source_mit_license_excludes_model_weights(self) -> None:
        license_file = ROOT / "LICENSE"
        self.assertTrue(license_file.is_file(), "source LICENSE is missing")
        text = license_file.read_text(encoding="utf-8").lower()
        self.assertIn("mit license", text)

        notices_file = ROOT / "THIRD_PARTY_NOTICES.md"
        self.assertTrue(notices_file.is_file(), "third-party notices are missing")
        notices = notices_file.read_text(encoding="utf-8").lower()
        self.assertIn("non-commercial", notices)
        self.assertIn("research", notices)

    def test_development_tools_do_not_embed_the_original_machine_path(self) -> None:
        private_path = "C:\\Users\\sampa\\glance-win"
        offenders = [
            str(path.relative_to(ROOT))
            for path in (ROOT / "tools").rglob("*.py")
            if private_path.lower() in path.read_text(encoding="utf-8").lower()
        ]
        self.assertEqual(offenders, [], f"hardcoded machine paths remain in: {offenders}")

    def test_model_license_is_staged_and_privately_restricted(self) -> None:
        model_license = ROOT / "packaging" / "MODEL.LICENSE"
        self.assertTrue(model_license.is_file())
        self.assertIn("non-commercial", model_license.read_text(encoding="utf-8"))
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
        self.assertIn("non-commercial research-only", notice)
        build = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("THIRD_PARTY_NOTICES.md", build)


class RuntimePathTests(unittest.TestCase):
    def test_packaged_launcher_sets_explicit_runtime_marker(self) -> None:
        source = (ROOT / "packaging" / "launch_glance.py").read_text(encoding="utf-8")
        self.assertIn("GLANCE_PACKAGED", source)
        from glance.paths import resolve_paths

        with mock.patch.dict(os.environ, {"GLANCE_PACKAGED": "1"}):
            paths = resolve_paths(project_root=Path("C:/source/glance"))
        self.assertTrue(paths.packaged)

    def test_packaged_paths_keep_mutable_data_outside_install_directory(self) -> None:
        from glance.paths import resolve_paths

        paths = resolve_paths(
            packaged=True,
            project_root=Path("C:/source/glance"),
            executable_dir=Path("C:/Users/test/AppData/Local/Programs/Glance"),
            local_app_data=Path("C:/Users/test/AppData/Local"),
        )
        self.assertEqual(paths.data_dir, Path("C:/Users/test/AppData/Local/Glance/data"))
        self.assertEqual(
            paths.models_dir,
            Path("C:/Users/test/AppData/Local/Programs/Glance/models/buffalo_s"),
        )
        self.assertNotEqual(paths.data_dir, paths.executable_dir)
        self.assertEqual(paths.agent_executable, paths.executable_dir / "GlanceAgent.exe")
        self.assertEqual(paths.grant_tool, paths.executable_dir / "provider" / "grant_tool.exe")
        self.assertEqual(paths.provider_dll, paths.executable_dir / "provider" / "GlanceProvider.dll")
        self.assertEqual(paths.admin_script, paths.executable_dir / "provider" / "GlanceAdmin.ps1")

    def test_source_paths_remain_project_local_and_creation_is_lazy(self) -> None:
        from glance.paths import resolve_paths

        root = Path("C:/source/glance")
        paths = resolve_paths(packaged=False, project_root=root)
        self.assertEqual(paths.data_dir, root / "data")
        self.assertEqual(paths.models_dir, root / "models" / "buffalo_s")
        self.assertEqual(paths.agent_executable, root / ".venv" / "Scripts" / "pythonw.exe")
        self.assertEqual(paths.grant_tool, root / "provider" / "build" / "grant_tool.exe")
        self.assertFalse(paths.data_dir.exists())

    def test_packaged_data_directory_creation_reports_os_errors(self) -> None:
        from glance.paths import resolve_paths

        paths = resolve_paths(
            packaged=True,
            project_root=Path("C:/source/glance"),
            executable_dir=Path("C:/install/Glance"),
            local_app_data=Path("C:/blocked"),
        )
        with mock.patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "denied"):
                paths.ensure_data_dir()

    def test_pipeline_reports_missing_model_paths_before_session_creation(self) -> None:
        from glance.core import GlancePipeline

        with self.assertRaisesRegex(FileNotFoundError, "Required face model is missing"):
            GlancePipeline(model_dir=Path("C:/absent/models/buffalo_s"))


class BuildManifestTests(unittest.TestCase):
    def test_runtime_and_build_dependencies_are_exactly_pinned(self) -> None:
        for filename in ("requirements.txt", "requirements-build.txt"):
            path = ROOT / filename
            self.assertTrue(path.is_file(), f"{filename} is missing")
            requirements = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            self.assertTrue(requirements, f"{filename} has no packages")
            for requirement in requirements:
                self.assertIn("==", requirement, f"requirement is not pinned: {requirement}")
        build_requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
        self.assertRegex(build_requirements, r"(?im)^Nuitka==4\.3\.1$")

    def test_windows_build_script_targets_nuitka_standalone_gui(self) -> None:
        script = ROOT / "packaging" / "build_windows.ps1"
        self.assertTrue(script.is_file(), "Windows build script is missing")
        text = script.read_text(encoding="utf-8").lower()
        for required in (
            "nuitka", "--standalone", "--windows-console-mode=disable",
            "--assume-yes-for-downloads", "glance.desktop.main",
        ):
            self.assertIn(required, text)
        self.assertIn("requirements-lock.txt", text)
        self.assertIn("collect_notices.py", text)
        self.assertIn("launch_agent.py", text)
        self.assertIn("glanceagent.exe", text)
        self.assertIn("glanceadmin.ps1", text)
        self.assertIn("privilegesrequired=admin", (ROOT / "packaging" / "inno" / "Glance.iss").read_text(encoding="utf-8").lower())

    def test_installer_uses_protected_program_files_and_bundles_provider_components(self) -> None:
        inno = (ROOT / "packaging" / "inno" / "Glance.iss").read_text(encoding="utf-8").lower()
        self.assertIn("defaultdirname={autopf}\\glance", inno)
        self.assertIn("privilegesrequired=admin", inno)
        build = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8").lower()
        for required in ("glanceprovider.dll", "grant_tool.exe", "glanceadmin.ps1", "glanceagent.exe"):
            self.assertIn(required, build)

    def test_skip_model_download_preserves_existing_staging_directory(self) -> None:
        script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertRegex(script, r"(?s)if\s*\(-not\s+\$SkipModelDownload\).*?Remove-Item")

    def test_python_version_probe_avoids_nested_quote_corruption(self) -> None:
        script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("sys.maxsize.bit_length()", script)

    def test_build_uses_canonical_version_for_executable_and_installer(self) -> None:
        version_file = ROOT / "VERSION"
        self.assertTrue(version_file.is_file(), "canonical VERSION file is missing")
        version = version_file.read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("/DAppVersion=$appVersion", script)
        self.assertIn("--product-version=$appVersion", script)


class AppIconTests(unittest.TestCase):
    def test_approved_artwork_is_checked_in_unchanged(self) -> None:
        from PIL import Image

        source = ROOT / "assets" / "GlanceIcon.png"
        self.assertTrue(source.is_file(), "approved Glance artwork is missing")
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest().upper(),
            "248C0B813200EA97DD4EB6A88E6D45D394F479AF271CD564FA4D0E9315490585",
            "the approved Glance artwork was modified",
        )
        with Image.open(source) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.width, image.height)
            self.assertGreaterEqual(min(image.size), 256)
            self.assertIn("A", image.getbands())

    def test_icon_tool_converts_approved_artwork_without_redrawing_it(self) -> None:
        from PIL import Image

        script = (ROOT / "tools" / "create_icon.py").read_text(encoding="utf-8")
        self.assertIn("GlanceIcon.png", script)
        self.assertNotIn("ImageDraw", script)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Glance.ico"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "create_icon.py"), str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with Image.open(output) as icon:
                self.assertEqual(
                    sorted(icon.ico.sizes()),
                    [(16, 16), (24, 24), (32, 32), (48, 48),
                     (64, 64), (128, 128), (256, 256)],
                )

    def test_readme_uses_the_supplied_download_banner(self) -> None:
        source = ROOT / "assets" / "GlanceDownload.png"
        self.assertTrue(source.is_file(), "download banner artwork is missing")
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest().upper(),
            "B4F2B5FB0048DE4A244A7CC3954395CCFE32785DCEFF604E92D23D9B5ADBCB53",
            "the supplied download banner was modified",
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("assets/GlanceDownload.png", readme)

    def test_build_and_installer_use_the_approved_icon(self) -> None:
        build = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("assets\\GlanceIcon.png", build)
        self.assertIn("--include-data-files=$iconSource=assets/GlanceIcon.png", build)
        self.assertEqual(build.count("--windows-icon-from-ico=$icon"), 2)
        inno = (ROOT / "packaging" / "inno" / "Glance.iss").read_text(encoding="utf-8")
        self.assertIn("SetupIconFile={#AppDist}\\Glance.ico", inno)


class InstallerPolicyTests(unittest.TestCase):
    def test_inno_setup_is_protected_but_does_not_enable_provider_during_install(self) -> None:
        script = ROOT / "packaging" / "inno" / "Glance.iss"
        self.assertTrue(script.is_file(), "Inno Setup script is missing")
        text = script.read_text(encoding="utf-8").lower()
        for required in (
            "privilegesrequired=admin", "minversion=10.0.22000", "{autopf}\\glance",
            "autoprograms", "autodesktop",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "regsvr32", "hklm", "schtasks", "programdata\\glance\\data",
        ):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("deletefile", text)
        self.assertIn("glanceadmin.ps1", text)
        self.assertIn("-operation disable-provider", text)
        self.assertIn("infobeforefile=..\\installer-info.txt", text)

    def test_installer_notice_separates_mit_source_and_restricted_weights(self) -> None:
        notice = ROOT / "packaging" / "installer-info.txt"
        self.assertTrue(notice.is_file(), "plain-text installer license notice is missing")
        text = notice.read_text(encoding="utf-8").lower()
        self.assertIn("mit", text)
        self.assertIn("non-commercial", text)
        self.assertIn("research-only", text)


class WorkflowPolicyTests(unittest.TestCase):
    def test_windows_workflow_builds_artifacts_without_publishing(self) -> None:
        workflow = ROOT / ".github" / "workflows" / "windows.yml"
        self.assertTrue(workflow.is_file(), "Windows artifact workflow is missing")
        text = workflow.read_text(encoding="utf-8").lower()
        for required in (
            "windows-2022", "pull_request", "requirements-lock.txt", "tests.test_desktop",
            "tests.test_cli", "tests.test_liveness", "tests.test_grant", "glancesetup.exe",
            "innosetup-6.7.3.exe", "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732",
        ):
            self.assertIn(required, text)
        for forbidden in ("softprops/action-gh-release", "gh release create", "contents: write"):
            self.assertNotIn(forbidden, text)

    def test_source_instructions_stage_models_where_source_runtime_reads_them(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("tools\\fetch_models.py models\\buffalo_s", readme)


if __name__ == "__main__":
    unittest.main()
