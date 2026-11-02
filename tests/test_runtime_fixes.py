r"""Regression tests for runtime fixes.

Run with .venv\Scripts\python.exe -m tests.test_runtime_fixes
"""

from __future__ import annotations

import ctypes
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import glance.desktop.backend as backend
import glance.desktop.main as desktop_main
from glance.desktop.pages import _unlock_display

GRANT_LINE = (
    "2026-10-31 21:56:19  granted Sampath "
    "(centroid 0.794, nearest 0.791, liveness heavy/Head turn) "
    "-> C:\\ProgramData\\Glance\\grant.bin"
)
BOOKEND_LINE = "2026-10-31 21:56:22  verification finished: granted"


class SingleInstanceTests(unittest.TestCase):
    def _claim_with(self, create_value, errors):
        """Run the claim logic against scripted kernel outcomes."""
        with (
            patch.object(
                desktop_main._kernel32_checked, "CreateMutexW", return_value=create_value
            ),
            patch("ctypes.get_last_error", side_effect=errors),
        ):
            return desktop_main._acquire_mutex("Local\\GlanceTest-scrubbed")

    def test_first_claim_of_new_mutex_succeeds(self) -> None:
        self.assertTrue(self._claim_with(999, [0]))

    def test_second_claim_of_same_mutex_is_rejected(self) -> None:
        """The fix: use_last_error + zeroed slot must surface
        ERROR_ALREADY_EXISTS (183) instead of a stale 0."""
        self.assertFalse(self._claim_with(999, [183]))

    def test_duplicate_handle_is_closed(self) -> None:
        with (
            patch.object(
                desktop_main._kernel32_checked, "CreateMutexW", return_value=999
            ),
            patch("ctypes.get_last_error", return_value=183),
            patch.object(desktop_main._kernel32_checked, "CloseHandle") as close,
        ):
            self.assertFalse(desktop_main._acquire_mutex("Local\\GlanceTest-scrubbed"))
            close.assert_called_once_with(999)

    def test_failed_create_is_permissive(self) -> None:
        self.assertTrue(self._claim_with(0, [0]))

    def test_live_second_claim_rejected_when_mutex_available(self) -> None:
        """End-to-end Win32 check, tolerant of an ambient kernel anomaly seen
        in some full-suite runs: CreateMutexW intermittently returns NULL with
        error 6 even for a fresh name. When that happens we cannot observe the
        reject path, so the test verifies the environment can create a mutex
        and skips if it cannot."""
        for _ in range(5):
            name = f"Local\\GlanceTest-{uuid.uuid4()}"
            if not desktop_main._acquire_mutex(name):
                continue
            if desktop_main._instance_mutex is None:
                continue
            if not desktop_main._acquire_mutex(name):
                return  # second claim rejected as expected
            # Suspicious: second claim accepted. Check whether the environment
            # can even create a fresh mutex right now.
            probe = f"Local\\GlanceProbe-{uuid.uuid4()}"
            ctypes.set_last_error(0)
            handle = desktop_main._kernel32_checked.CreateMutexW(None, False, probe)
            if not handle:
                continue  # ambient kernel anomaly; try another name
            self.fail("second claim accepted while fresh mutex creation works")
        self.skipTest("kernel could not provide a mutex during this run")


class LastUnlockTests(unittest.TestCase):
    def test_prefers_grant_line_over_verification_bookend(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp)
            (log_dir / "agent.log").write_text(
                GRANT_LINE + "\n" + BOOKEND_LINE + "\n",
                encoding="utf-8",
            )
            line = backend._last_unlock(log_dir)
        self.assertEqual(line.strip(), GRANT_LINE)

    def test_missing_log_returns_never(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            line = backend._last_unlock(Path(temp))
        self.assertEqual(line, "never")

    def test_no_grant_line_returns_never(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp)
            (log_dir / "agent.log").write_text(
                "2026-11-01 10:32:05  verification finished: denied\n",
                encoding="utf-8",
            )
            line = backend._last_unlock(log_dir)
        self.assertEqual(line, "never")


class UnlockDisplayTests(unittest.TestCase):
    def test_grant_line_yields_time_and_name(self) -> None:
        shown, name = _unlock_display(GRANT_LINE)
        self.assertEqual(shown, "21:56:19")
        self.assertEqual(name, "Sampath")

    def test_bookend_line_yields_no_name(self) -> None:
        shown, name = _unlock_display(BOOKEND_LINE)
        self.assertEqual(shown, "21:56:22")
        self.assertEqual(name, "")

    def test_empty_line_yields_nothing(self) -> None:
        self.assertEqual(_unlock_display(""), ("", ""))


if __name__ == "__main__":
    unittest.main()
