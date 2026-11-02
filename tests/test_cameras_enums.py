r"""Camera/display enumeration helpers (Mac-parity step 3)."""

from __future__ import annotations

import ctypes
import subprocess
import unittest
from unittest.mock import patch

from glance.desktop import cameras


class CameraNameEnumerationTests(unittest.TestCase):
    def test_parses_powershell_names(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout="Camera A\nCamera B\n", stderr="")
        with patch.object(cameras.subprocess, "run", return_value=done):
            self.assertEqual(cameras.enum_camera_names(), ["Camera A", "Camera B"])

    def test_nonzero_exit_returns_empty(self) -> None:
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with patch.object(cameras.subprocess, "run", return_value=done):
            self.assertEqual(cameras.enum_camera_names(), [])

    def test_subprocess_error_returns_empty(self) -> None:
        with patch.object(cameras.subprocess, "run", side_effect=OSError("no powershell")):
            self.assertEqual(cameras.enum_camera_names(), [])

    def test_empty_output_returns_empty(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout="   \n", stderr="")
        with patch.object(cameras.subprocess, "run", return_value=done):
            self.assertEqual(cameras.enum_camera_names(), [])


class MonitorEnumerationTests(unittest.TestCase):
    def test_primary_and_external_labels(self) -> None:
        calls = []

        def fake_get_monitor_info(monitor, info):
            calls.append(monitor)
            contents = ctypes.cast(info, ctypes.POINTER(cameras._MONITORINFO)).contents
            if monitor == 1:
                contents.dwFlags = cameras._MONITORINFOF_PRIMARY
            else:
                contents.dwFlags = 0
            return True

        def fake_enum(_hdc, _rect, callback, _data):
            callback(1, None, None, 0)
            callback(2, None, None, 0)
            return True

        with (
            patch.object(cameras._user32, "EnumDisplayMonitors", side_effect=fake_enum),
            patch.object(cameras._user32, "GetMonitorInfoW", side_effect=fake_get_monitor_info),
        ):
            labels = cameras.monitor_names()
        self.assertEqual(labels, ["Main display", "External display"])

    def test_fallback_when_enumeration_fails(self) -> None:
        with patch.object(cameras._user32, "EnumDisplayMonitors", side_effect=OSError("boom")):
            self.assertEqual(cameras.monitor_names(), ["Main display"])


if __name__ == "__main__":
    unittest.main()
