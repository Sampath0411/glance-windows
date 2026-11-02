r"""Mac-parity step 1: face-screen controls and their data model.

Covers the per-identity enabled toggle, recapture (replace, never append),
capture-quality tiers/percent, and the fail-closed agent scope rules.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from glance import core
from glance.agent.glance_agent import resolve_unlock_scope
from glance.desktop import camera
from glance.desktop.enrollment import (
    QUALITY_FAIR,
    QUALITY_POOR,
    EnrollmentSession,
    quality_percent,
    quality_tier,
)


def vector(seed: int, dim: int = 512) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / float(np.linalg.norm(v))


class IdentityStoreMigrationTests(unittest.TestCase):
    def write_legacy(self, path: Path, identities) -> Path:
        payload = {"version": 1, "model": "arcface-w600k_mbf-v1",
                   "identities": identities}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_legacy_plain_array_samples_become_unrated_and_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_legacy(Path(temp) / "identities.json", [
                {"name": "Anu", "samples": [vector(1).tolist(), vector(2).tolist()]},
            ])
            store = core.IdentityStore(path)
            self.assertEqual(len(store.identities), 1)
            identity = store.identities[0]
            self.assertTrue(identity.enabled)
            self.assertEqual(len(identity.samples), 2)
            self.assertTrue(all(sample.quality is None for sample in identity.samples))

    def test_round_trip_keeps_enabled_and_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "identities.json"
            store = core.IdentityStore(path)
            store.add_sample("Sampath", vector(3), quality=0.62)
            store.add_sample("Sampath", vector(4), quality=0.31)
            store.identities[0].enabled = False
            store.save()

            reloaded = core.IdentityStore(path)
            identity = reloaded.identities[0]
            self.assertFalse(identity.enabled)
            self.assertEqual([s.quality for s in identity.samples], [0.62, 0.31])

    def test_missing_enabled_defaults_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_legacy(Path(temp) / "identities.json", [
                {"name": "Anu", "samples": [vector(1).tolist()]},
            ])
            store = core.IdentityStore(path)
            self.assertTrue(store.identities[0].enabled)


class BestMatchEnabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.embedding = vector(10)

    def make_store(self, enabled) -> core.IdentityStore:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "identities.json"
            store = core.IdentityStore(path)
            store.add_sample("Anu", self.embedding, quality=0.9)
            store.identities[0].enabled = enabled
            return store

    def test_disabled_identity_is_never_matched(self) -> None:
        store = self.make_store(enabled=False)
        self.assertIsNone(store.best_match(self.embedding, threshold=0.4))

    def test_enabled_identity_matches(self) -> None:
        store = self.make_store(enabled=True)
        match = store.best_match(self.embedding, threshold=0.4)
        self.assertIsNotNone(match)
        self.assertEqual(match[0].name, "Anu")


class RecaptureTests(unittest.TestCase):
    def test_replace_samples_swaps_wholesale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = core.IdentityStore(Path(temp) / "identities.json")
            store.add_sample("Anu", vector(1), quality=0.9)
            replaced = store.replace_samples("Anu", [vector(5), vector(6)], [0.4, 0.7])
            self.assertEqual(len(replaced.samples), 2)
            self.assertEqual([s.quality for s in replaced.samples], [0.4, 0.7])
            self.assertAlmostEqual(
                float(np.dot(store.identities[0].samples[0].embedding, vector(5))),
                1.0, places=4,
            )


class QualityTierTests(unittest.TestCase):
    def test_tiers_match_mac_cutoffs(self) -> None:
        self.assertEqual(quality_tier(0.39), "poor")
        self.assertEqual(quality_tier(0.40), "fair")
        self.assertEqual(quality_tier(0.49), "fair")
        self.assertEqual(quality_tier(0.50), "good")
        self.assertEqual(quality_tier(1.0), "good")
        self.assertIsNone(quality_tier(None))

    def test_percent_is_mac_formula(self) -> None:
        qualities = [0.3, 0.3, 0.3] + [0.6] * 15
        self.assertEqual(quality_percent(qualities), 83)  # 15/18

    def test_unrated_samples_never_count_as_poor(self) -> None:
        self.assertEqual(quality_percent([None, None, 0.2, 0.9]), 50)
        self.assertIsNone(quality_percent([None, None]))
        self.assertEqual(quality_percent([]), None)


class CaptureQualityTests(unittest.TestCase):
    def test_good_combo_scores_good(self) -> None:
        quality = camera.capture_quality(face_fraction=0.05, blur=70.0, brightness=130.0)
        self.assertGreaterEqual(quality, QUALITY_FAIR)

    def test_small_blurry_face_scores_poor(self) -> None:
        quality = camera.capture_quality(face_fraction=0.01, blur=5.0, brightness=20.0)
        self.assertLess(quality, QUALITY_POOR)

    def test_returns_bounded_01(self) -> None:
        for fraction in (0.0, 0.02, 0.04, 0.10):
            for blur in (0.0, 30.0, 60.0, 120.0):
                for brightness in (0.0, 130.0, 255.0):
                    quality = camera.capture_quality(fraction, blur, brightness)
                    self.assertGreaterEqual(quality, 0.0)
                    self.assertLessEqual(quality, 1.0)


class EnrollmentSessionQualityTests(unittest.TestCase):
    def test_samples_store_quality(self) -> None:
        session = EnrollmentSession()
        ok, _ = session.accept(vector(1), (0.0, 0.0), quality=0.72)
        self.assertTrue(ok)
        self.assertEqual(session.samples[0][1], 0.72)
        self.assertEqual(session.qualities, [0.72])


class AgentScopeFailClosedTests(unittest.TestCase):
    class Person:
        def __init__(self, name, enabled):
            self.name = name
            self.enabled = enabled

    def test_no_identities_fails_closed(self) -> None:
        store = type("Store", (), {"identities": []})()
        self.assertEqual(
            resolve_unlock_scope(store, any_identity=True, identity=None),
            "no enrolled identities",
        )

    def test_all_disabled_fails_closed(self) -> None:
        store = type("Store", (), {
            "identities": [self.Person("Anu", False), self.Person("Max", False)],
        })()
        self.assertEqual(
            resolve_unlock_scope(store, any_identity=True, identity=None),
            "all enrolled faces are disabled",
        )

    def test_selected_identity_disabled_fails_closed(self) -> None:
        store = type("Store", (), {
            "identities": [self.Person("Anu", False), self.Person("Max", True)],
        })()
        self.assertEqual(
            resolve_unlock_scope(store, any_identity=False, identity="Anu"),
            "selected identity is disabled",
        )

    def test_selected_identity_not_enrolled_fails_closed(self) -> None:
        store = type("Store", (), {
            "identities": [self.Person("Anu", False), self.Person("Max", True)],
        })()
        self.assertEqual(
            resolve_unlock_scope(store, any_identity=False, identity="Nobody"),
            "selected identity is not enrolled",
        )

    def test_selected_identity_enabled_proceeds(self) -> None:
        store = type("Store", (), {
            "identities": [self.Person("Anu", True), self.Person("Max", False)],
        })()
        self.assertIsNone(resolve_unlock_scope(store, any_identity=False, identity="Anu"))

    def test_any_mode_proceeds_with_one_enabled(self) -> None:
        store = type("Store", (), {
            "identities": [self.Person("Anu", False), self.Person("Max", True)],
        })()
        self.assertIsNone(resolve_unlock_scope(store, any_identity=True, identity=None))


if __name__ == "__main__":
    unittest.main()
