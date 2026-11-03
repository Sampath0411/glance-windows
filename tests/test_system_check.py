from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path

from glance.desktop import system_check
from glance.desktop import main as desktop_main


class SystemCompatibilityTests(unittest.TestCase):
    def test_windows_11_x64_at_minimum_build_is_supported(self) -> None:
        result = system_check.evaluate_system("Windows", 22000, "AMD64")
        self.assertTrue(result.supported)
        self.assertEqual(result.blockers, ())

    def test_windows_10_build_is_blocked_with_actionable_reason(self) -> None:
        result = system_check.evaluate_system("Windows", 19045, "AMD64")
        self.assertFalse(result.supported)
        self.assertIn("Windows 11", " ".join(result.blockers))

    def test_non_x64_system_is_blocked(self) -> None:
        result = system_check.evaluate_system("Windows", 22631, "ARM64")
        self.assertFalse(result.supported)
        self.assertIn("64-bit x64", " ".join(result.blockers))

    def test_non_windows_system_is_blocked(self) -> None:
        result = system_check.evaluate_system("Linux", 0, "x86_64")
        self.assertFalse(result.supported)
        self.assertIn("Windows", " ".join(result.blockers))

    def test_runtime_facts_use_windows_api_version_and_architecture(self) -> None:
        class Version:
            build = 22631

        with (
            patch.object(system_check.sys, "platform", "win32"),
            patch.object(system_check.sys, "getwindowsversion", return_value=Version()),
            patch.object(system_check.platform, "machine", return_value="AMD64"),
        ):
            result = system_check.check_current_system()
        self.assertTrue(result.supported)

    def test_unsupported_pc_shows_actionable_blocking_dialog(self) -> None:
        result = system_check.evaluate_system("Windows", 19045, "AMD64")
        with (
            patch.object(system_check, "check_current_system", return_value=result),
            patch.object(desktop_main.QMessageBox, "critical") as critical,
        ):
            self.assertFalse(desktop_main.ensure_compatible_pc())
        self.assertIn("This app can’t run on this PC", critical.call_args.args[2])
        self.assertIn("Windows 11", critical.call_args.args[2])

    def test_supported_pc_continues_without_dialog(self) -> None:
        result = system_check.evaluate_system("Windows", 22631, "AMD64")
        with (
            patch.object(system_check, "check_current_system", return_value=result),
            patch.object(desktop_main.QMessageBox, "critical") as critical,
        ):
            self.assertTrue(desktop_main.ensure_compatible_pc())
        critical.assert_not_called()

    def test_installer_gates_supported_windows_architecture_and_admin(self) -> None:
        installer = Path(__file__).resolve().parents[1] / "packaging" / "inno" / "Glance.iss"
        source = installer.read_text(encoding="utf-8")
        self.assertIn("MinVersion=10.0.22000", source)
        self.assertIn("ArchitecturesAllowed=x64", source)
        self.assertIn("PrivilegesRequired=admin", source)
        self.assertIn("This app can't run on this PC", source)


if __name__ == "__main__":
    unittest.main()
