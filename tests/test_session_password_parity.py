r"""Mac-parity step 4: password screen parity.

Covers the session lifetime state, the stand-alone remove-password path, and
the separate window-autostart registry control.
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from glance import protection_state
from glance.desktop import backend


class SessionLifetimeTests(unittest.TestCase):
    def test_new_session_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            self.assertFalse(protection_state.session_is_unlocked(path))

    def test_unlocked_until_future_is_unlocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            self.assertTrue(protection_state.set_session_unlocked(path, until=2**40))
            self.assertTrue(protection_state.session_is_unlocked(path))

    def test_expired_session_relocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            protection_state.set_session_unlocked(path, until=1)
            self.assertFalse(protection_state.session_is_unlocked(path, now=2000))

    def test_never_expires_when_until_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            protection_state.set_session_unlocked(path, until=0)
            self.assertTrue(protection_state.session_is_unlocked(path, now=2**40))

    def test_lock_session_clears(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            protection_state.set_session_unlocked(path, until=2**40)
            self.assertTrue(protection_state.lock_session(path))
            self.assertFalse(protection_state.session_is_unlocked(path))

    def test_session_state_is_separate_from_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            protection_state.set_session_unlocked(path, until=2**40)
            protection_state.set_paused_until_wake(path, True)
            self.assertTrue(protection_state.session_is_unlocked(path))
            self.assertTrue(protection_state.is_paused_until_wake(path))


class RemovePasswordTests(unittest.TestCase):
    def test_missing_helper_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing-grant-tool.exe"
            with patch.object(backend, "GRANT_TOOL", missing):
                ok, message = backend.clear_stored_password()
        self.assertFalse(ok)
        self.assertIn("missing", message)

    def test_clear_runs_grant_tool_and_invalidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tool = Path(temp) / "grant_tool.exe"
            tool.write_text("stub", encoding="utf-8")
            with (
                patch.object(backend, "GRANT_TOOL", tool),
                patch.object(backend, "_run", return_value=(True, "cleared")) as run,
                patch.object(backend, "invalidate") as invalidate,
            ):
                ok, message = backend.clear_stored_password()
            self.assertTrue(ok)
            run.assert_called_once_with([str(tool), "clear"])
            invalidate.assert_called_once_with("credential")


class WindowAutostartTests(unittest.TestCase):
    class FakeKey:
        def __init__(self, store=None):
            self.store = store or {}
            self.deleted = []

        def SetValueEx(self, name, _reserved, _kind, value):
            self.store[name] = value

        def DeleteValue(self, name):
            self.deleted.append(name)
            self.store.pop(name, None)

        def QueryValueEx(self, name):
            if name not in self.store:
                raise FileNotFoundError(name)
            return self.store[name], 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    @contextmanager
    def _patch_winreg(self, key):
        def set_value(key_obj, name, _reserved, _kind, value):
            key_obj.store[name] = value

        def delete_value(key_obj, name):
            key_obj.deleted.append(name)
            key_obj.store.pop(name, None)

        def query_value(key_obj, name):
            if name not in key_obj.store:
                raise FileNotFoundError(name)
            return key_obj.store[name], 1

        fake = type("FakeWinreg", (), {
            "HKEY_CURRENT_USER": object(),
            "OpenKey": lambda *_a, **_k: key,
            "SetValueEx": set_value,
            "DeleteValue": delete_value,
            "QueryValueEx": query_value,
            "KEY_SET_VALUE": 1,
            "KEY_QUERY_VALUE": 2,
            "REG_SZ": 1,
        })
        with patch.dict("sys.modules", {"winreg": fake}):
            yield key

    def test_enable_writes_run_value(self) -> None:
        key = self.FakeKey()
        with self._patch_winreg(key):
            ok, message = backend.set_window_autostart(True)
        self.assertTrue(ok)
        self.assertIn("Glance Desktop", key.store)
        self.assertIn("pythonw.exe", key.store["Glance Desktop"])

    def test_disable_removes_run_value(self) -> None:
        key = self.FakeKey({"Glance Desktop": "x"})
        with self._patch_winreg(key):
            ok, _ = backend.set_window_autostart(False)
        self.assertTrue(ok)
        self.assertIn("Glance Desktop", key.deleted)

    def test_enabled_state_reads_registry(self) -> None:
        key = self.FakeKey({"Glance Desktop": "x"})
        with self._patch_winreg(key):
            self.assertTrue(backend.window_autostart_enabled())
        key2 = self.FakeKey()
        with self._patch_winreg(key2):
            self.assertFalse(backend.window_autostart_enabled())


if __name__ == "__main__":
    unittest.main()
