import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from glance.agent.glance_agent import SessionWatcher, is_locked_desktop_name
from glance import protection_state


class PowerResumeTests(unittest.TestCase):
    def test_only_winlogon_desktop_is_treated_as_locked(self):
        self.assertTrue(is_locked_desktop_name("Winlogon"))
        self.assertFalse(is_locked_desktop_name("Default"))
        self.assertFalse(is_locked_desktop_name(None))

    def make_watcher(self, locked, config_path=None):
        verifier = SimpleNamespace(paths=SimpleNamespace(config_path=config_path))
        watcher = SessionWatcher(verifier=verifier)
        watcher.locked = locked
        return watcher

    def test_resume_restarts_verification_when_session_is_locked(self):
        watcher = self.make_watcher(locked=True)
        with (
            patch.object(watcher, "_stop_verification") as stop,
            patch.object(watcher, "_start_verification") as start,
        ):
            watcher.on_power_resume()
        stop.assert_called_once_with()
        start.assert_called_once_with()

    def test_resume_does_not_open_camera_when_session_is_unlocked(self):
        watcher = self.make_watcher(locked=False)
        with (
            patch.object(watcher, "_stop_verification") as stop,
            patch.object(watcher, "_start_verification") as start,
        ):
            watcher.on_power_resume()
        stop.assert_called_once_with()
        start.assert_not_called()

    def test_repeated_resume_notifications_do_not_restart_camera_repeatedly(self):
        watcher = self.make_watcher(locked=True)
        with (
            patch("glance.agent.glance_agent.time.monotonic", side_effect=[10.0, 10.5]),
            patch.object(watcher, "_stop_verification") as stop,
            patch.object(watcher, "_start_verification") as start,
        ):
            watcher.on_power_resume()
            watcher.on_power_resume()
        stop.assert_called_once_with()
        start.assert_called_once_with()

    def test_wake_clears_a_manual_pause_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "agent_config.json"
            state = config.parent / "protection_state.json"
            protection_state.set_paused_until_wake(state, True)
            watcher = self.make_watcher(locked=False, config_path=config)
            with patch.object(watcher, "_stop_verification"):
                watcher.on_power_resume()
            self.assertFalse(protection_state.is_paused_until_wake(state))

    def test_new_agent_after_wake_scans_only_if_windows_is_still_locked(self):
        watcher = self.make_watcher(locked=False)
        with patch.object(watcher, "_start_verification") as start:
            watcher.start_after_resume(is_locked=True)
        self.assertTrue(watcher.locked)
        start.assert_called_once_with()

        unlocked = self.make_watcher(locked=False)
        with patch.object(unlocked, "_start_verification") as start:
            unlocked.start_after_resume(is_locked=False)
        self.assertFalse(unlocked.locked)
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
