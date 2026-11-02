r"""Mac-parity step 5: face-store DPAPI sealing.

Covers protect/unprotect round-trips, the sealed file format, legacy plaintext
migration with a backup, and fail-closed behavior on corruption.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from glance import core
from glance import dpapi


def vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / float(np.linalg.norm(v))


class DpapiRoundTripTests(unittest.TestCase):
    def test_protect_unprotect_round_trip(self) -> None:
        payload = b"face data here"
        sealed = dpapi.protect(payload)
        self.assertNotIn(payload, sealed)
        self.assertEqual(dpapi.unprotect(sealed), payload)

    def test_protect_refuses_empty(self) -> None:
        with self.assertRaises(ValueError):
            dpapi.protect(b"")

    def test_unprotect_garbage_raises(self) -> None:
        with self.assertRaises(OSError):
            dpapi.unprotect(b"not a dpapi blob at all")


class IdentityStoreSealingTests(unittest.TestCase):
    def make_plaintext(self, path: Path) -> Path:
        payload = {
            "version": 1,
            "model": "arcface-w600k_mbf-v1",
            "identities": [
                {"name": "Anu", "samples": [vector(1).tolist(), vector(2).tolist()]},
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_save_produces_sealed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "identities.json"
            store = core.IdentityStore(path)
            store.add_sample("Anu", vector(1), quality=0.8)
            store.save()
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(core.SEALED_MAGIC))
            self.assertTrue(store.sealed)

    def test_round_trip_through_sealed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "identities.json"
            store = core.IdentityStore(path)
            store.add_sample("Anu", vector(5), quality=0.6)
            store.save()
            reloaded = core.IdentityStore(path)
            self.assertEqual(len(reloaded.identities), 1)
            self.assertEqual(reloaded.identities[0].name, "Anu")
            self.assertEqual(reloaded.identities[0].samples[0].quality, 0.6)

    def test_legacy_plaintext_migrates_and_backs_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_plaintext(Path(temp) / "identities.json")
            original = path.read_bytes()
            store = core.IdentityStore(path)
            self.assertEqual(len(store.identities), 1)
            self.assertTrue(store.sealed)
            backup = path.with_suffix(path.suffix + ".bak")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_bytes(), original)
            # Second load reads the sealed form fine.
            self.assertEqual(len(core.IdentityStore(path).identities), 1)

    def test_legacy_without_enabled_defaults_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_plaintext(Path(temp) / "identities.json")
            store = core.IdentityStore(path)
            self.assertTrue(store.identities[0].enabled)

    def test_corrupt_random_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "identities.json"
            path.write_bytes(b"this is not json, not a sealed payload")
            with self.assertRaises(RuntimeError):
                core.IdentityStore(path)

    def test_tampered_sealed_payload_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "identities.json"
            store = core.IdentityStore(path)
            store.add_sample("Anu", vector(2), quality=0.5)
            store.save()
            raw = bytearray(path.read_bytes())
            raw[-1] ^= 0xFF  # damage the sealed blob
            path.write_bytes(bytes(raw))
            with self.assertRaises(Exception):
                core.IdentityStore(path)
            # The corrupt file was never replaced by plaintext.
            self.assertTrue(path.read_bytes().startswith(core.SEALED_MAGIC))


if __name__ == "__main__":
    unittest.main()
