import tempfile
import unittest
from pathlib import Path

from glance import protection_state


class ProtectionStateTests(unittest.TestCase):
    def test_manual_pause_is_remembered_until_wake(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            protection_state.set_paused_until_wake(path, True)
            self.assertTrue(protection_state.is_paused_until_wake(path))
            protection_state.clear_pause_on_wake(path)
            self.assertFalse(protection_state.is_paused_until_wake(path))

    def test_corrupt_pause_state_defaults_to_not_paused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "protection_state.json"
            path.write_text("broken", encoding="utf-8")
            self.assertFalse(protection_state.is_paused_until_wake(path))


if __name__ == "__main__":
    unittest.main()
