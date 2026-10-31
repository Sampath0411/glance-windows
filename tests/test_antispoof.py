"""Anti-spoof tests: deny cues, confirm cues, and the agent's fail-closed path.

The bug these lock down: the agent used to run liveness in LIGHT mode and pass
``None`` for blink history, so three clean frames of a still photo confirmed the
scan and wrote a grant. These tests prove that a static photo or a static phone
screen can no longer reach ``write_grant`` while a blink or real movement can.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from glance import liveness as lv
from glance.agent import glance_agent
from glance.desktop import backend


def evaluator(mode: lv.LivenessMode, tuning: lv.LivenessTuning | None = None):
    return lv.LivenessEvaluator(mode=mode, tuning=tuning)


def clean_readings(overrides: dict | None = None) -> dict:
    readings = {cue: lv.CueReading.none() for cue in lv.Cue}
    readings.update(overrides or {})
    return readings


class ModeTests(unittest.TestCase):
    def test_default_mode_is_protective(self) -> None:
        self.assertIs(lv.DEFAULT_MODE, lv.LivenessMode.HEAVY)

    def test_unknown_mode_falls_back_to_heavy_not_off(self) -> None:
        for value in ("", None, "off", "sometimes", 3):
            with self.subTest(value=value):
                self.assertIs(lv.LivenessMode.from_name(value), lv.LivenessMode.HEAVY)

    def test_all_modes_round_trip(self) -> None:
        for mode in lv.LivenessMode:
            self.assertIs(lv.LivenessMode.from_name(mode.value), mode)

    def test_heavy_stays_pending_without_a_confirm_cue(self) -> None:
        ev = evaluator(lv.LivenessMode.HEAVY)
        snapshot = None
        for _ in range(10):
            snapshot = ev.observe(clean_readings())
        self.assertIs(snapshot.decision, lv.Decision.PENDING)

    def test_light_still_confirms_for_diagnostics(self) -> None:
        ev = evaluator(lv.LivenessMode.LIGHT)
        for _ in range(3):
            snapshot = ev.observe(clean_readings())
        self.assertIs(snapshot.decision, lv.Decision.CONFIRMED)

    def test_strict_ignores_motion_and_blink(self) -> None:
        ev = evaluator(lv.LivenessMode.STRICT)
        motion = lv.CueReading(1.0, 1.0)
        for _ in range(6):
            snapshot = ev.observe(clean_readings({
                lv.Cue.MICRO_MOTION: motion,
                lv.Cue.BLINK: motion,
            }))
        self.assertIs(snapshot.decision, lv.Decision.PENDING)

    def test_heavy_and_strict_confirm_on_head_turn(self) -> None:
        ev = evaluator(lv.LivenessMode.STRICT)
        head_turn = lv.CueReading(1.0, 1.0)
        snapshot = ev.observe(clean_readings({lv.Cue.HEAD_TURN: head_turn}))
        self.assertIs(snapshot.decision, lv.Decision.CONFIRMED)
        self.assertIs(snapshot.confirmed_by, lv.Cue.HEAD_TURN)

        ev = evaluator(lv.LivenessMode.HEAVY)
        snapshot = ev.observe(clean_readings({lv.Cue.HEAD_TURN: head_turn}))
        self.assertIs(snapshot.decision, lv.Decision.CONFIRMED)

    def test_waiting_reason_names_head_turn_challenge(self) -> None:
        snapshot = evaluator(lv.LivenessMode.HEAVY).observe(clean_readings())
        self.assertIn("left", snapshot.waiting_reason())
        self.assertIn("right", snapshot.waiting_reason())

    def test_every_deny_cue_overrides_blink(self) -> None:
        deny_cues = [cue for cue in lv.Cue if cue.role is lv.CueRole.DENY]
        self.assertGreaterEqual(len(deny_cues), 4)
        for cue in deny_cues:
            with self.subTest(cue=cue):
                ev = evaluator(lv.LivenessMode.HEAVY)
                borrowed = lv.LivenessTuning()
                frames = max(borrowed.frames(c) for c in deny_cues)
                snapshot = None
                for _ in range(frames + 1):
                    snapshot = ev.observe(clean_readings({lv.Cue.HEAD_TURN: lv.CueReading(1.0, 1.0), cue: lv.CueReading(1.0, 1.0)}))
                self.assertIs(snapshot.decision, lv.Decision.DENIED)
                self.assertIs(snapshot.denied_by, cue)
                self.assertTrue(snapshot.denial_reason())


class BlinkTests(unittest.TestCase):
    def test_constant_open_eyes_never_count_as_a_blink(self) -> None:
        history = [0.20, 0.20, 0.19, 0.20, 0.21, 0.20, 0.19, 0.20]
        self.assertEqual(lv.blink_reading(history).confidence, 0.0)

    def test_landmark_jitter_is_not_a_blink(self) -> None:
        history = [0.20, 0.19, 0.20, 0.185, 0.20, 0.195, 0.20, 0.19]
        self.assertEqual(lv.blink_reading(history).confidence, 0.0)

    def test_bilateral_dip_and_recovery_is_a_blink(self) -> None:
        history = [0.20, 0.21, 0.05, 0.09, 0.20, 0.21]
        reading = lv.blink_reading(history)
        self.assertGreaterEqual(reading.level, 0.5)
        self.assertGreater(reading.confidence, 0.0)

    def test_a_dip_without_recovery_does_not_count(self) -> None:
        history = [0.20, 0.21, 0.05, 0.05, 0.05, 0.05]
        self.assertEqual(lv.blink_reading(history).confidence, 0.0)

    def test_a_dip_too_small_to_be_a_blink_does_not_count(self) -> None:
        history = [0.05, 0.05, 0.035, 0.05, 0.05]
        self.assertEqual(lv.blink_reading(history).confidence, 0.0)

    def test_pixel_openness_drops_when_the_eye_is_closed(self) -> None:
        open_eye = _synthetic_aligned(open_eye=True)
        closed_eye = _synthetic_aligned(open_eye=False)
        opening = lv.eye_openness(open_eye, 60.0)
        closing = lv.eye_openness(closed_eye, 60.0)
        self.assertIsNotNone(opening)
        self.assertIsNotNone(closing)
        self.assertLess(closing.openness, opening.openness * 0.62,
                        f"open={opening} closed={closing}")

    def test_small_faces_abstain(self) -> None:
        sample = lv.eye_openness(_synthetic_aligned(open_eye=True), 10.0)
        self.assertIsNotNone(sample)
        self.assertLess(sample.confidence, lv.MIN_OPENNESS_CONFIDENCE)


class HeadTurnTrackerTests(unittest.TestCase):
    @staticmethod
    def feed(tracker, yaw_values, confidence=1.0):
        reading = lv.CueReading.none()
        for yaw in yaw_values:
            reading = tracker.observe(lv.HeadPoseSample(yaw, confidence))
        return reading

    def test_forward_left_right_forward_sequence_confirms(self):
        tracker = lv.HeadTurnTracker()
        self.feed(tracker, [0.0] * 4)
        self.assertEqual(tracker.phase, "left")
        self.feed(tracker, [-0.18] * 3)
        self.assertEqual(tracker.phase, "right")
        self.feed(tracker, [0.18] * 3)
        self.assertEqual(tracker.phase, "return")
        reading = self.feed(tracker, [0.0] * 3)
        self.assertEqual(reading, lv.CueReading(1.0, 1.0))
        self.assertEqual(tracker.phase, "complete")

    def test_each_turn_requires_three_stable_observations(self):
        tracker = lv.HeadTurnTracker()
        self.feed(tracker, [0.0] * 4)
        self.feed(tracker, [-0.18] * 2)
        self.assertEqual(tracker.phase, "left")
        self.feed(tracker, [-0.18])
        self.assertEqual(tracker.phase, "right")

    def test_static_pose_and_wrong_turn_order_do_not_confirm(self):
        static = lv.HeadTurnTracker()
        self.feed(static, [0.0] * 20)
        self.assertFalse(static.confirmed)

        wrong_order = lv.HeadTurnTracker()
        self.feed(wrong_order, [0.0] * 4)
        self.feed(wrong_order, [0.18] * 3)
        self.feed(wrong_order, [-0.18] * 3)
        self.feed(wrong_order, [0.0] * 4)
        self.assertFalse(wrong_order.confirmed)

    def test_off_center_initial_pose_must_return_forward_before_challenge(self):
        tracker = lv.HeadTurnTracker()
        self.feed(tracker, [0.18] * 4)
        self.assertEqual(tracker.phase, "baseline")
        self.feed(tracker, [0.0] * 4)
        self.assertEqual(tracker.phase, "left")

    def test_small_yaw_and_low_quality_sample_do_not_confirm(self):
        tracker = lv.HeadTurnTracker()
        self.feed(tracker, [0.0] * 4)
        self.feed(tracker, [-0.03] * 4)
        self.assertEqual(tracker.phase, "left")
        tracker.observe(lv.HeadPoseSample(-0.2, 0.0))
        self.assertEqual(tracker.phase, "quality")
        self.assertFalse(tracker.confirmed)

    def test_pose_extraction_uses_normalized_nose_offset(self):
        forward = np.array([[40, 50], [80, 50], [60, 70],
                            [48, 90], [72, 90]], dtype=np.float32)
        turned = forward.copy()
        turned[2, 0] -= 8.0
        forward_sample = lv.extract_head_pose_sample(forward)
        turned_sample = lv.extract_head_pose_sample(turned)
        self.assertIsNotNone(forward_sample)
        self.assertIsNotNone(turned_sample)
        self.assertAlmostEqual(forward_sample.yaw, 0.0, places=5)
        self.assertLess(turned_sample.yaw, -0.15)

    def test_mirrored_pose_extraction_reverses_left_right_sign(self):
        turned = np.array([[40, 50], [80, 50], [52, 70],
                           [48, 90], [72, 90]], dtype=np.float32)
        normal = lv.extract_head_pose_sample(turned)
        mirrored = lv.extract_head_pose_sample(turned, mirrored=True)
        self.assertAlmostEqual(normal.yaw, -mirrored.yaw, places=5)

    def test_pose_extraction_rejects_invalid_landmarks(self):
        self.assertIsNone(lv.extract_head_pose_sample(None))
        self.assertIsNone(lv.extract_head_pose_sample(np.zeros((5, 2), dtype=np.float32)))
        invalid = np.array([[40, 50], [80, 50], [np.nan, 70],
                            [48, 90], [72, 90]], dtype=np.float32)
        self.assertIsNone(lv.extract_head_pose_sample(invalid))


class MotionTests(unittest.TestCase):
    @staticmethod
    def base():
        return np.array([[100.0, 100.0], [160.0, 100.0], [130.0, 130.0],
                         [112.0, 160.0], [148.0, 160.0]], dtype=np.float32)

    def test_ratios_ignore_translation_rotation_and_scale(self) -> None:
        original = lv.landmark_ratios(self.base())
        theta = np.radians(31.0)
        rotation = np.array([[np.cos(theta), -np.sin(theta)],
                             [np.sin(theta), np.cos(theta)]], dtype=np.float32)
        moved = self.base() @ rotation.T * 2.7 + np.array([40.0, -25.0], dtype=np.float32)
        np.testing.assert_allclose(lv.landmark_ratios(moved), original, atol=1e-4)

    def test_static_ratio_history_abstains(self) -> None:
        base = lv.landmark_ratios(self.base())
        rng = np.random.default_rng(7)
        history = [base * (1 + rng.normal(0, 0.0006, base.shape)) for _ in range(10)]
        self.assertLess(lv.micro_motion_reading(history, 60.0).level, 0.6)

    def test_real_expression_change_is_micro_motion(self) -> None:
        base = lv.landmark_ratios(self.base())
        history = []
        for step in range(10):
            scale = 1.0 + 0.08 * step / 9.0  # mouth widening over ~0.6 s
            history.append(base * np.array([1.0, 1.0, scale, 1.0 + 0.01 * step / 9.0, 1.0]))
        reading = lv.micro_motion_reading(history, 60.0)
        self.assertGreaterEqual(reading.level, 0.6)
        self.assertGreater(reading.confidence, 0.0)

    def test_small_face_abstains(self) -> None:
        base = lv.landmark_ratios(self.base())
        history = [base * (1 + 0.05 * i / 9) for i in range(10)]
        self.assertEqual(lv.micro_motion_reading(history, 10.0).confidence, 0.0)


class ScreenGlowTests(unittest.TestCase):
    @staticmethod
    def frame():
        return np.full((480, 640, 3), 90, dtype=np.uint8)

    def test_dark_room_ring_is_not_a_screen(self) -> None:
        frame = self.frame()
        sample = lv.extract_screen_glow(frame, (250.0, 160.0, 390.0, 330.0))
        self.assertIsNotNone(sample)
        self.assertLess(lv.screen_glow_reading(sample).level, 0.62)

    def test_bright_flat_neutral_ring_is_a_screen(self) -> None:
        frame = np.full((480, 640, 3), 242, dtype=np.uint8)
        sample = lv.extract_screen_glow(frame, (250.0, 160.0, 390.0, 330.0))
        self.assertIsNotNone(sample)
        self.assertGreaterEqual(lv.screen_glow_reading(sample).level, 0.62)

    def test_bright_but_colourful_ring_is_not_trusted(self) -> None:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, :] = (40, 40, 240)  # saturated red wall
        sample = lv.extract_screen_glow(frame, (250.0, 160.0, 390.0, 330.0))
        self.assertLess(lv.screen_glow_reading(sample).level, 0.62)

    def test_small_faces_abstain(self) -> None:
        frame = np.full((480, 640, 3), 242, dtype=np.uint8)
        sample = lv.extract_screen_glow(frame, (300.0, 200.0, 330.0, 240.0))
        self.assertLess(lv.screen_glow_reading(sample).confidence,
                        lv.MIN_SCREEN_CONFIDENCE)


class TextureTests(unittest.TestCase):
    def test_smooth_skin_has_no_grid(self) -> None:
        crop = np.full((180, 160, 3), 140, dtype=np.uint8)
        crop = cv2.GaussianBlur(crop, (9, 9), 3)
        sample = lv.extract_texture(crop)
        self.assertIsNotNone(sample)
        self.assertLess(lv.texture_reading(sample).level, 0.6)

    def test_periodic_grid_trips_moire(self) -> None:
        crop = np.full((180, 160, 3), 140, dtype=np.float32)
        yy, xx = np.mgrid[0:180, 0:160]
        grating = 30.0 * np.sin(2 * np.pi * (xx / 5.0 + yy / 7.0))
        crop = np.clip(crop + grating[:, :, None], 0, 255).astype(np.uint8)
        sample = lv.extract_texture(crop)
        self.assertIsNotNone(sample)
        self.assertGreaterEqual(lv.texture_reading(sample).level, 0.6)

    def test_tiny_crops_abstain(self) -> None:
        crop = np.zeros((40, 40, 3), dtype=np.uint8)
        self.assertIsNone(lv.extract_texture(crop))


class DeviceTests(unittest.TestCase):
    def test_plain_background_has_no_device(self) -> None:
        frame = np.tile(np.linspace(50, 190, 640, dtype=np.uint8), (480, 1))
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        observation = lv.detect_device_rectangle(frame, (270.0, 160.0, 370.0, 330.0))
        self.assertIsNone(observation.rectangle)

    def test_phone_screen_enclosing_the_face_is_a_device(self) -> None:
        frame = np.full((480, 640, 3), 60, dtype=np.uint8)
        cv2.rectangle(frame, (215, 95), (425, 405), (245, 245, 245), -1)
        cv2.rectangle(frame, (215, 95), (425, 405), (20, 20, 20), 14)
        face = (270.0, 160.0, 370.0, 330.0)
        observation = lv.detect_device_rectangle(frame, face)
        self.assertIsNotNone(observation.rectangle)
        self.assertGreaterEqual(lv.device_reading(observation).level, 0.15)

    def test_small_far_face_inside_a_large_doorway_is_not_a_phone(self) -> None:
        frame = np.full((480, 640, 3), 70, dtype=np.uint8)
        cv2.rectangle(frame, (60, 30), (580, 470), (205, 205, 205), 8)
        face = (295.0, 210.0, 345.0, 290.0)  # face is a small part of the frame
        observation = lv.detect_device_rectangle(frame, face)
        self.assertIsNone(observation.rectangle)

    def test_background_rectangle_crossing_face_edge_is_not_a_device(self) -> None:
        frame = np.full((480, 640, 3), 70, dtype=np.uint8)
        cv2.rectangle(frame, (330, 120), (540, 360), (220, 220, 220), 12)
        face = (270.0, 160.0, 370.0, 330.0)
        observation = lv.detect_device_rectangle(frame, face)
        self.assertIsNone(observation.rectangle)


class SettingsTests(unittest.TestCase):
    def test_liveness_defaults_to_heavy(self) -> None:
        self.assertEqual(backend.Settings().liveness, "heavy")

    def test_liveness_round_trips_through_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "agent_config.json"
            with patch("glance.desktop.backend.CONFIG_PATH", config):
                backend.Settings(liveness="strict").save()
                self.assertEqual(backend.Settings.load().liveness, "strict")

    def test_invalid_liveness_on_disk_becomes_heavy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "agent_config.json"
            config.write_text(json.dumps({"liveness": "off"}), encoding="utf-8")
            with patch("glance.desktop.backend.CONFIG_PATH", config):
                self.assertEqual(backend.Settings.load().liveness, "heavy")


# --------------------------------------------------------------------------- #
# agent integration: a spoof must never write a grant
# --------------------------------------------------------------------------- #
def _synthetic_aligned(open_eye: bool, size: int = 112) -> np.ndarray:
    rng = np.random.default_rng(11)
    skin = np.full((size, size, 3), 150, dtype=np.int16)
    skin += rng.normal(0, 6, skin.shape).astype(np.int16)
    image = np.clip(skin, 0, 255).astype(np.uint8)
    scale = size / 112.0
    for cx, cy in lv.EYE_CENTERS_112:
        x1, x2 = int((cx - 11) * scale), int((cx + 11) * scale)
        y1, y2 = int((cy - 7) * scale), int((cy + 7) * scale)
        if open_eye:
            image[y1:y2, x1:x2] = 205
            image[y1 + 3:y2 - 3, x1 + 7:x2 - 7] = 45
        else:
            image[y1:y2, x1:x2] = 150
            # A thin lash line, as a closed eye actually looks: the dark area
            # collapses but does not disappear.
            row = int(cy * scale)
            image[row:row + 1, x1:x2] = 80
    return image


class _FakeCamera:
    def __init__(self, frames: int) -> None:
        self.frames = frames
        self.reads = 0
        self.stop: threading.Event | None = None

    def isOpened(self) -> bool:
        return True

    def read(self):
        self.reads += 1
        if self.reads >= self.frames and self.stop is not None:
            self.stop.set()
        return True, _synthetic_frame()

    def release(self) -> None:
        pass


def _synthetic_frame(size: int = 320) -> np.ndarray:
    ramp = np.tile(np.linspace(55, 170, size, dtype=np.float32), (size, 1))
    return cv2.cvtColor(ramp.astype(np.uint8), cv2.COLOR_GRAY2BGR)


class _FakePipeline:
    """Feeds the real cue extractors a controlled sequence of frames."""

    def __init__(self, frames: int, script, motion: float = 0.0, head_turn_series=None,
                 identity_series=None) -> None:
        self.camera = _FakeCamera(frames)
        self.script = script
        self.motion = motion
        self.head_turn_series = head_turn_series
        self.identity_series = identity_series
        self.calls = 0
        self.person = SimpleNamespace(name="Sampath")
        self.current_person = self.person
        self.store = SimpleNamespace(
            identities=[self.person],
            best_match=lambda embedding, threshold: (self.current_person, 0.9, 0.9),
        )

    def open_camera(self, index: int = 0):
        return self.camera

    def embedding_for(self, frame):
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        if self.identity_series:
            name = self.identity_series[min(self.calls - 1, len(self.identity_series) - 1)]
            if name != self.person.name:
                self.current_person = SimpleNamespace(name=name)
                self.store.identities.append(self.current_person)
            else:
                self.current_person = self.person
        kps = np.array([[120.0, 110.0], [180.0, 110.0], [150.0, 145.0],
                        [130.0, 180.0], [170.0, 180.0]], dtype=np.float32)
        if self.head_turn_series:
            yaw = self.head_turn_series[min(self.calls - 1, len(self.head_turn_series) - 1)]
            kps[2, 0] = 150.0 + yaw * 60.0
        if self.motion:
            offset = self.motion * np.sin(self.calls * 1.1)
            kps[3, 0] -= offset
            kps[4, 0] += offset
        face = SimpleNamespace(bbox=(100.0, 70.0, 220.0, 220.0),
                               area=120.0 * 150.0, kps=kps)
        return np.ones(8, dtype=np.float32), face, self.script[index]

    def run(self, **kwargs):
        raise AssertionError("the fake pipeline is only used by the verifier")


def _run_verifier(script, frames=8, motion=0.0, liveness="heavy", head_turn_series=None,
                  identity_series=None, any_identity=False):
    stop = threading.Event()
    pipeline = _FakePipeline(frames, script, motion, head_turn_series, identity_series)
    pipeline.camera.stop = stop
    writer = SimpleNamespace(calls=0)

    def write_grant():
        writer.calls += 1
        return "grant"

    paths = SimpleNamespace(identities_path=Path("identities.json"))
    with (
        patch.object(glance_agent, "GlancePipeline", return_value=pipeline),
        patch.object(glance_agent.grant_mod, "write_grant", side_effect=write_grant),
        patch.object(glance_agent, "log"),
    ):
        result = glance_agent.Verifier(
            identity="Sampath", any_identity=any_identity, require_stable=1,
            timeout=5.0, liveness=liveness, paths=paths,
        ).run(stop)
    return result, writer


class DesktopLivenessTests(unittest.TestCase):
    def setUp(self):
        from glance.desktop.camera import _GateVerifier
        self.gate = _GateVerifier()
        self.open = _synthetic_aligned(open_eye=True)
        self.closed = _synthetic_aligned(open_eye=False)
        pipeline = _FakePipeline(1, [self.open])
        self.frame = _synthetic_frame()
        _, self.face, _ = pipeline.embedding_for(self.frame)

    def observe(self, aligned, name="Sampath", yaw=0.0):
        self.face.kps[2, 0] = 150.0 + yaw * 60.0
        return self.gate.verify(self.frame, self.face, aligned, name, .9)

    def head_turn(self):
        result = None
        for yaw, count in ((0.0, 4), (-.18, 3), (.18, 3), (0.0, 3)):
            for _ in range(count):
                result = self.observe(self.open, yaw=yaw)
        return result

    def test_static_face_is_withheld_but_head_turn_releases_match(self):
        for _ in range(12):
            self.assertEqual(self.observe(self.open)[0], "")
        self.assertEqual(self.head_turn()[0], "Sampath")

    def test_identity_change_cannot_inherit_a_head_turn(self):
        self.assertEqual(self.head_turn()[0], "Sampath")
        self.assertEqual(self.observe(self.open, "Other")[0], "")

    def test_face_loss_cannot_preserve_a_head_turn(self):
        self.assertEqual(self.head_turn()[0], "Sampath")
        self.gate.reset_identity()
        self.assertEqual(self.observe(self.open)[0], "")

    def test_spoof_denial_latches_until_a_new_attempt(self):
        with patch.object(lv, "extract_screen_glow",
                          return_value=lv.ScreenSample(240., 2., .98, .001, 200.)):
            for _ in range(4):
                self.observe(self.open)
        self.gate.reset_identity()
        name, score, hint = self.head_turn()
        self.assertEqual(name, "")
        self.assertEqual(score, 0.)
        self.assertIn("screen", hint.lower())


class AgentIntegrationTests(unittest.TestCase):
    def test_verifier_defaults_to_heavy(self) -> None:
        verifier = glance_agent.Verifier(
            identity="Sampath", paths=SimpleNamespace(identities_path=Path("x")),
        )
        self.assertIs(verifier.liveness, lv.LivenessMode.HEAVY)

    def test_static_photo_with_landmark_jitter_does_not_grant(self):
        result, writer = _run_verifier(
            [_synthetic_aligned(open_eye=True)], frames=12, motion=6.0,
        )
        self.assertEqual(result, "denied")
        self.assertEqual(writer.calls, 0)

    def test_forward_left_right_forward_sequence_grants(self):
        yaws = [0.0] * 4 + [-.18] * 3 + [.18] * 3 + [0.0] * 3
        script = [_synthetic_aligned(True)] * len(yaws)
        result, writer = _run_verifier(
            script, frames=len(yaws),
            head_turn_series=yaws,
        )
        self.assertEqual(result, "granted")
        self.assertEqual(writer.calls, 1)

    def test_identity_change_resets_head_turn_progress(self):
        yaws = [0.0] * 4 + [-.18] * 3 + [.18] * 3 + [0.0] * 3
        identities = ["Sampath"] * 7 + ["Anu"] * 6
        script = [_synthetic_aligned(True)] * len(yaws)
        result, writer = _run_verifier(
            script, frames=len(yaws), head_turn_series=yaws,
            identity_series=identities, any_identity=True,
        )
        self.assertNotEqual(result, "granted")
        self.assertEqual(writer.calls, 0)

    def test_static_photo_never_writes_a_grant(self) -> None:
        still = _synthetic_aligned(open_eye=True)
        result, writer = _run_verifier([still])
        self.assertEqual(result, "denied")
        self.assertEqual(writer.calls, 0)

    def test_heavy_rejects_landmark_motion_on_a_static_photo(self) -> None:
        result, writer = _run_verifier(
            [_synthetic_aligned(open_eye=True)], frames=12, motion=6.0,
        )
        self.assertEqual(result, "denied")
        self.assertEqual(writer.calls, 0)

    def test_diagnostic_light_mode_cannot_authorize_windows_unlock(self) -> None:
        result, writer = _run_verifier(
            [_synthetic_aligned(open_eye=True)], liveness="light",
        )
        self.assertEqual(result, "denied")
        self.assertEqual(writer.calls, 0)

    def test_phone_glow_denies_the_scan(self) -> None:
        still = _synthetic_aligned(open_eye=True)
        with patch.object(glance_agent.lv, "extract_screen_glow",
                          return_value=lv.ScreenSample(240.0, 2.0, 0.98, 0.001, 200.0)):
            result, writer = _run_verifier([still], frames=6)
        self.assertEqual(result, "denied")
        self.assertEqual(writer.calls, 0)

    def test_strict_refuses_movement_but_accepts_head_turn(self) -> None:
        still = _synthetic_aligned(open_eye=True)
        result, writer = _run_verifier([still], frames=12, motion=6.0, liveness="strict")
        self.assertEqual(result, "denied")
        self.assertEqual(writer.calls, 0)

        yaws = [0.0] * 4 + [-.18] * 3 + [.18] * 3 + [0.0] * 3
        script = [_synthetic_aligned(True)] * len(yaws)
        result, writer = _run_verifier(script, frames=len(yaws), liveness="strict",
                                       head_turn_series=yaws)
        self.assertEqual(result, "granted")
        self.assertEqual(writer.calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

