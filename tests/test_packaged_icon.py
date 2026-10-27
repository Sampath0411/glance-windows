from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.create_icon import create_icon


class PackagedIconTests(unittest.TestCase):
    def test_generated_icon_contains_multiresolution_windows_icon(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "Glance.ico"
            create_icon(output)
            self.assertTrue(output.is_file())
            from PIL import Image

            with Image.open(output) as icon:
                self.assertEqual(icon.format, "ICO")
                self.assertIn((64, 64), icon.ico.sizes())


if __name__ == "__main__":
    unittest.main()
