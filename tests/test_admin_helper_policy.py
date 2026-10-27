from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminHelperPolicyTests(unittest.TestCase):
    def test_admin_helper_is_protected_and_accepts_only_fixed_operations(self) -> None:
        script_path = ROOT / "provider" / "GlanceAdmin.ps1"
        self.assertTrue(script_path.is_file(), "protected admin helper is missing")
        script = script_path.read_text(encoding="utf-8").lower()
        for operation in ("install-provider", "disable-provider", "reset-glance", "status"):
            self.assertIn(operation, script)
        self.assertIn("validateset", script.lower())
        self.assertIn("programfiles", script)
        for forbidden in ("invoke-expression", "cmd /c", "iex "):
            self.assertNotIn(forbidden, script)

    def test_admin_helper_checks_identity_and_expected_payload_paths(self) -> None:
        script = (ROOT / "provider" / "GlanceAdmin.ps1").read_text(encoding="utf-8")
        lowered = script.lower()
        for required in (
            "$targetsid", "profilelist", "glanceagent.exe", "grant_tool.exe",
            "glanceprovider.dll", "credential providers", "credential provider filters",
            "glance agent",
        ):
            self.assertIn(required, lowered)


if __name__ == "__main__":
    unittest.main()
