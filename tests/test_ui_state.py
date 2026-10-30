import json
import tempfile
import unittest
from pathlib import Path

from glance.desktop import ui_state


class UiStateTests(unittest.TestCase):
    def test_whats_new_is_shown_once_per_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ui_state.json"
            self.assertTrue(ui_state.should_show_whats_new("0.1.4", path))
            ui_state.mark_whats_new_shown("0.1.4", path)
            self.assertFalse(ui_state.should_show_whats_new("0.1.4", path))
            self.assertTrue(ui_state.should_show_whats_new("0.1.5", path))

    def test_later_dismisses_startup_prompt_without_repeating(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ui_state.json"
            self.assertTrue(ui_state.should_offer_startup_prompt(path))
            ui_state.dismiss_startup_prompt(path)
            self.assertFalse(ui_state.should_offer_startup_prompt(path))

    def test_invalid_state_is_replaced_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ui_state.json"
            path.write_text("not json", encoding="utf-8")
            self.assertTrue(ui_state.should_show_whats_new("0.1.4", path))
            ui_state.mark_whats_new_shown("0.1.4", path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["whats_new_version"], "0.1.4")


if __name__ == "__main__":
    unittest.main()
