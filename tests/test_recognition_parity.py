r"""Mac-parity step 2: recognition + general settings.

Covers the new Settings keys and their clamps, threshold preservation on load,
the detection-distance mapping used by the agent, the face-check scan window,
and animation/auto-retry wiring.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from glance.agent.glance_agent import Verifier, resolve_runtime_paths
from glance.core import DETECTION_DISTANCE_FRACTIONS
from glance.desktop.backend import DETECTION_DISTANCE_DEFAULT, MATCH_CONFIDENCE_STOPS
from glance.desktop.backend import Settings
from glance.desktop.face_gate import resolve_scan_seconds


class SettingsDefaultsTests(unittest.TestCase):
    def test_new_install_defaults(self) -> None:
        settings = Settings()
        self.assertEqual(settings.threshold, 0.60)
        self.assertEqual(settings.detection_distance, DETECTION_DISTANCE_DEFAULT)
        self.assertEqual(settings.face_detection_seconds, 5)
        self.assertTrue(settings.liveness_enabled)
        self.assertTrue(settings.animation_enabled)
        self.assertEqual(settings.animation_style, "original")
        self.assertFalse(settings.auto_retry_once)

    def test_legacy_config_without_new_keys_loads_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            path.write_text(json.dumps({"threshold": 0.60}), encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual(settings.threshold, 0.60)
        self.assertEqual(settings.detection_distance, DETECTION_DISTANCE_DEFAULT)
        self.assertEqual(settings.face_detection_seconds, 5)
        self.assertTrue(settings.liveness_enabled)
        self.assertTrue(settings.animation_enabled)
        self.assertEqual(settings.animation_style, "original")
        self.assertFalse(settings.auto_retry_once)

    def test_load_preserves_existing_threshold(self) -> None:
        # Loading must never silently alter a stored threshold.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            path.write_text(json.dumps({"threshold": 0.72}), encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual(settings.threshold, 0.72)

    def test_threshold_clamped_to_slider_range(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            path.write_text(json.dumps({"threshold": 0.99}), encoding="utf-8")
            settings = Settings.load(path)
        self.assertLessEqual(settings.threshold, 0.85)

    def test_unknown_detection_distance_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            path.write_text(json.dumps({"detection_distance": "banana"}), encoding="utf-8")
            settings = Settings.load(path)
        self.assertEqual(settings.detection_distance, DETECTION_DISTANCE_DEFAULT)

    def test_scan_seconds_clamped_to_3_10(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            path.write_text(json.dumps({"face_detection_seconds": 99}), encoding="utf-8")
            settings = Settings.load(path)
            self.assertEqual(settings.face_detection_seconds, 10)
            path.write_text(json.dumps({"face_detection_seconds": 1}), encoding="utf-8")
            settings = Settings.load(path)
            self.assertEqual(settings.face_detection_seconds, 3)


class DetectionDistanceMappingTests(unittest.TestCase):
    def test_close_default_far_map_to_area_fractions(self) -> None:
        self.assertEqual(
            DETECTION_DISTANCE_FRACTIONS,
            {"close": 0.020, "default": 0.012, "far": 0.008},
        )

    def test_verifier_uses_stop_fraction(self) -> None:
        paths = resolve_runtime_paths(packaged=False)
        close = Verifier(detection_distance="close", paths=paths)
        far = Verifier(detection_distance="far", paths=paths)
        default = Verifier(detection_distance="", paths=paths)
        self.assertEqual(close.min_face_fraction, 0.020)
        self.assertEqual(far.min_face_fraction, 0.008)
        self.assertEqual(default.min_face_fraction, 0.012)

    def test_match_confidence_stops_are_mac_numbers(self) -> None:
        self.assertEqual(MATCH_CONFIDENCE_STOPS, {
            "less_strict": 0.58,
            "default": 0.63,
            "more_strict": 0.68,
        })


class ScanSecondsTests(unittest.TestCase):
    class Settings:
        face_detection_seconds = 7

    def test_reads_setting(self) -> None:
        self.assertEqual(resolve_scan_seconds(self.Settings()), 7.0)

    def test_clamps(self) -> None:
        self.assertEqual(resolve_scan_seconds(type("S", (), {"face_detection_seconds": 2})()), 3.0)
        self.assertEqual(resolve_scan_seconds(type("S", (), {"face_detection_seconds": 25})()), 10.0)

    def test_falls_back_to_default(self) -> None:
        self.assertEqual(resolve_scan_seconds(type("S", (), {})()), 5.0)
        self.assertEqual(resolve_scan_seconds(type("S", (), {"face_detection_seconds": "x"})()), 5.0)


class SettingsRoundTripTests(unittest.TestCase):
    def test_all_new_fields_survive_config_save_load(self) -> None:
        settings = Settings(
            detection_distance="close",
            face_detection_seconds=8,
            liveness_enabled=False,
            animation_enabled=False,
            animation_style="minimal",
            auto_retry_once=True,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agent_config.json"
            settings.save(path)
            reloaded = Settings.load(path)
        self.assertEqual(reloaded.detection_distance, "close")
        self.assertEqual(reloaded.face_detection_seconds, 8)
        self.assertFalse(reloaded.liveness_enabled)
        self.assertFalse(reloaded.animation_enabled)
        self.assertEqual(reloaded.animation_style, "minimal")
        self.assertTrue(reloaded.auto_retry_once)


if __name__ == "__main__":
    unittest.main()
