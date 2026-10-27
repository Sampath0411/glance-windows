from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.collect_notices import collect_notices


class CollectNoticesTests(unittest.TestCase):
    def test_copies_runtime_package_license_and_third_party_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "notices"
            index = collect_notices(output, ("onnxruntime", "opencv-python", "PySide6"))
            self.assertTrue(index.is_file())
            self.assertIn("onnxruntime", index.read_text(encoding="utf-8").lower())
            self.assertTrue(any(output.rglob("ThirdPartyNotices.txt")))
            self.assertTrue(any(output.rglob("LICENSE-3RD-PARTY.txt")))
            self.assertTrue(any(output.rglob("CPython-LICENSE.txt")))


if __name__ == "__main__":
    unittest.main()
