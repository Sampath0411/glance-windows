from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from glance.agent.grant import clear_pending_grant


class PendingGrantTests(unittest.TestCase):
    def test_clear_pending_grant_removes_only_the_named_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            grant = root / "grant.bin"
            unrelated = root / "agent.log"
            grant.write_bytes(b"old authorization")
            unrelated.write_text("keep", encoding="utf-8")

            self.assertTrue(clear_pending_grant(grant))

            self.assertFalse(grant.exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_clear_pending_grant_is_idempotent_when_file_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            grant = Path(temp) / "grant.bin"
            self.assertTrue(clear_pending_grant(grant))


if __name__ == "__main__":
    unittest.main()
