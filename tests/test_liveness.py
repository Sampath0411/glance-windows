"""
Phase 2 liveness / anti-spoof tests, run against a real captured frame.

Coverage:
  1. real face      - none of the four DENY cues fire on a genuine frame
  2. device bezel   - a synthetic phone screen around the face must trip it
  3. glare          - a synthetic flat specular blob must trip it
  4. screen glow    - a bright, flat, neutral panel must trip it
  5. print / moire  - a periodic halftone/pixel grid must trip it
  6. blink          - pixel openness collapses when the eyes are closed, and a
                      dip-and-recovery is recognised as a blink
  7. micro-motion   - rigid landmark ratios stay constant for a still photo and
                      vary when the face actually moves
  8. evaluator      - deny overrides confirm; light/heavy/strict semantics;
                      firing latches; confidence 0 abstains

Run:  .venv\\Scripts\\python.exe -m tests.test_liveness
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

from glance.core import FaceDetector
from glance import liveness as lv
from glance.paths import PATHS

DATA = PATHS.data_dir
FRAME = DATA / "selftest" / "frame.png"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def describe(reading: lv.CueReading) -> str:
    return f"level={reading.level:.3f} conf={reading.confidence:.2f}"


def clean_readings() -> dict:
    return {cue: lv.CueReading.none() for cue in lv.Cue}


def main() -> int:
    frame = cv2.imread(str(FRAME))
    if frame is None:
        print(f"missing {FRAME}", file=sys.stderr)
        return 1
    face = FaceDetector().detect(frame)[0]
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    pad = int(0.15 * max(x2 - x1, y2 - y1))
    crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
    aligned = cv2.resize(crop, (112, 112))
    interocular = lv.interocular_distance(face.kps)
    print(f"frame {frame.shape[1]}x{frame.shape[0]}, face bbox {face.bbox.round(0)}\n")

    # ---------------------------------------------------------------- 1. real
    print("1. real face, no spoof")
    glare = lv.extract_glare(crop)
    device = lv.detect_device_rectangle(frame, tuple(face.bbox))
    screen = lv.extract_screen_glow(frame, tuple(face.bbox))
    texture = lv.extract_texture(crop)
    gloss_r = lv.gloss_glare_reading(glare)
    device_r = lv.device_reading(device)
    screen_r = lv.screen_glow_reading(screen)
    texture_r = lv.texture_reading(texture)
    print(f"   glare:  fraction={glare.specular_fraction:.4f} cluster={glare.specular_cluster_ratio:.3f} "
          f"-> {describe(gloss_r)}")
    print(f"   device: rect={device.rectangle} overlap={device.face_overlap_fraction} "
          f"-> {describe(device_r)}")
    print(f"   screen: luma={screen.ring_luma:.0f} sat={screen.ring_saturation:.0f} "
          f"uniform={screen.ring_uniformity:.2f} -> {describe(screen_r)}")
    print(f"   texture: peak={texture.peak_ratio:.1f} periodic={texture.periodic_energy:.3f} "
          f"contrast={texture.contrast:.2f} -> {describe(texture_r)}")
    tuning = lv.LivenessTuning()
    check("real face does not trip glare", gloss_r.level < tuning.gloss_level,
          describe(gloss_r))
    check("real face does not trip device", (device_r.confidence == 0) or
          (device_r.level < tuning.device_level), describe(device_r))
    check("real face does not trip screen glow", screen_r.level < tuning.screen_level,
          describe(screen_r))
    check("real face does not trip print/moire", texture_r.level < tuning.moire_level,
          describe(texture_r))

    # ------------------------------------------------------------- 2. device
    print("\n2. synthetic phone held up (emissive screen displaying the captured photo)")
    h, w = frame.shape[:2]
    spoof = frame.copy()
    px1, py1 = max(0, x1 - 90), max(0, y1 - 70)
    px2, py2 = min(w - 1, x2 + 90), min(h - 1, y2 + 120)

    # A phone showing a photo: emissive bright screen, the face inside it, dark bezel.
    cv2.rectangle(spoof, (px1, py1), (px2, py2), (250, 250, 250), -1)
    inset = 26
    screen_crop = frame[py1 + inset:py2 - inset, px1 + inset:px2 - inset]
    if screen_crop.size:
        screen_crop = cv2.resize(screen_crop, (px2 - px1 - 2 * inset, py2 - py1 - 2 * inset))
        screen_crop = cv2.convertScaleAbs(screen_crop, alpha=1.05, beta=18)  # backlit screen
        spoof[py1 + inset:py2 - inset, px1 + inset:px2 - inset] = screen_crop
    cv2.rectangle(spoof, (px1, py1), (px2, py2), (18, 18, 18), 16)  # bezel
    cv2.imwrite(str(DATA / "selftest" / "spoof_device.png"), spoof)

    spoof_face = FaceDetector().detect(spoof)
    box = tuple(spoof_face[0].bbox) if spoof_face else tuple(face.bbox)
    d2 = lv.detect_device_rectangle(spoof, box)
    r2 = lv.device_reading(d2)
    print(f"   rect={d2.rectangle} overlap={d2.face_overlap_fraction} -> {describe(r2)}")
    check("device bezel detected on spoof", r2.level >= tuning.device_level,
          describe(r2))

    # -------------------------------------------------------------- 3. glare
    print("\n3. synthetic glare (large flat specular blob on the face)")
    glare_crop = crop.copy()
    ch, cw = glare_crop.shape[:2]
    cv2.ellipse(glare_crop, (cw // 2, ch // 3), (cw // 4, ch // 6), 0, 0, 360, (255, 255, 255), -1)
    cv2.imwrite(str(DATA / "selftest" / "spoof_glare.png"), glare_crop)
    g3 = lv.extract_glare(glare_crop)
    r3 = lv.gloss_glare_reading(g3)
    print(f"   fraction={g3.specular_fraction:.4f} cluster={g3.specular_cluster_ratio:.3f} "
          f"-> {describe(r3)}")
    check("glare detected on spoof", r3.level >= tuning.gloss_level, describe(r3))

    # -------------------------------------------------------- 4. screen glow
    print("\n4. synthetic emissive panel (bright, colour-neutral, flat)")
    panel = np.full_like(frame, 242)
    s4 = lv.extract_screen_glow(panel, tuple(face.bbox))
    r4 = lv.screen_glow_reading(s4)
    print(f"   luma={s4.ring_luma:.0f} sat={s4.ring_saturation:.0f} "
          f"uniform={s4.ring_uniformity:.2f} -> {describe(r4)}")
    check("screen glow detected on emissive panel", r4.level >= tuning.screen_level,
          describe(r4))

    saturated = np.zeros_like(frame)
    saturated[:, :] = (40, 40, 240)  # saturated red wall: bright but not a screen
    r4b = lv.screen_glow_reading(lv.extract_screen_glow(saturated, tuple(face.bbox)))
    check("bright saturated wall is not called a screen", r4b.level < tuning.screen_level,
          describe(r4b))

    # ------------------------------------------------------ 5. print / moire
    print("\n5. synthetic periodic grid (printed halftone or display pixel grid)")
    moire = crop.astype(np.float32)
    mh, mw = moire.shape[:2]
    yy, xx = np.mgrid[0:mh, 0:mw]
    moire += (26.0 * np.sin(2 * np.pi * (xx / 5.0 + yy / 7.0)))[:, :, None]
    moire = np.clip(moire, 0, 255).astype(np.uint8)
    cv2.imwrite(str(DATA / "selftest" / "spoof_moire.png"), moire)
    t5 = lv.extract_texture(moire)
    r5 = lv.texture_reading(t5)
    print(f"   peak={t5.peak_ratio:.1f} periodic={t5.periodic_energy:.3f} "
          f"contrast={t5.contrast:.2f} -> {describe(r5)}")
    check("periodic print/screen texture detected", r5.level >= tuning.moire_level,
          describe(r5))

    # --------------------------------------------------------------- 6. blink
    print("\n6. blink from pixel eye openness")
    eye_open = lv.eye_openness(aligned, interocular)
    closed = aligned.copy()
    mask = np.zeros(aligned.shape[:2], np.uint8)
    for cx, cy in lv.EYE_CENTERS_112:
        cv2.ellipse(mask, (int(cx), int(cy)),
                    (int(lv.EYE_HALF_112[0]), int(lv.EYE_HALF_112[1])), 0, 0, 360, 255, -1)
    skin_colour = tuple(int(v) for v in np.median(aligned[70:90, 20:100].reshape(-1, 3), axis=0))
    closed[mask > 0] = skin_colour
    cv2.imwrite(str(DATA / "selftest" / "spoof_closed_eyes.png"), closed)
    eye_closed = lv.eye_openness(closed, interocular)
    print(f"   open: {eye_open.openness:.3f}  closed: {eye_closed.openness:.3f}")
    check("closed eyes are measurably darker-free than open eyes",
          eye_closed.openness < eye_open.openness * 0.62,
          f"{eye_closed.openness:.3f} vs {eye_open.openness:.3f}")

    flat = [eye_open.openness] * 6
    check("a still photo never blinks", lv.blink_reading(flat).confidence == 0.0)
    dip = [eye_open.openness] * 2 + [eye_closed.openness] * 2 + [eye_open.openness] * 2
    check("a dip-and-recovery reads as a blink", lv.blink_reading(dip).confidence > 0.0)

    # -------------------------------------------------------- 7. micro-motion
    print("\n7. micro-motion from rigid landmark ratios")
    base = lv.landmark_ratios(face.kps)
    rng = np.random.default_rng(3)
    still_history = [base * (1 + rng.normal(0, 0.0006, base.shape)) for _ in range(10)]
    still_r = lv.micro_motion_reading(still_history, interocular)
    moving_history = [base * (1 + 0.09 * i / 9.0 * np.array([0.1, 0.1, 1.0, 0.2, 0.2]))
                      for i in range(10)]
    moving_r = lv.micro_motion_reading(moving_history, interocular)
    print(f"   still: {describe(still_r)}  moving: {describe(moving_r)}")
    check("a held photo has no micro-motion", still_r.level < tuning.motion_level,
          describe(still_r))
    check("a real expression has micro-motion", moving_r.level >= tuning.motion_level,
          describe(moving_r))

    # ---------------------------------------------------------- 8. evaluator
    print("\n8. evaluator semantics")
    ev = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    snaps = [ev.observe(clean_readings()) for _ in range(3)]
    check("light mode pending before minimum frames", snaps[0].decision is lv.Decision.PENDING,
          snaps[0].decision.value)
    check("light mode confirms after 3 clean frames", snaps[2].decision is lv.Decision.CONFIRMED,
          snaps[2].decision.value)

    ev2 = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    deny = clean_readings()
    deny[lv.Cue.DEVICE_DETECTED] = lv.CueReading(0.9, 1.0)
    out = None
    for _ in range(3):
        out = ev2.observe(deny)
    check("deny overrides confirm", out.decision is lv.Decision.DENIED,
          out.denied_by.value if out.denied_by else "")
    check("denial reason is user-facing", bool(out.denial_reason()), out.denial_reason() or "")

    ev3 = lv.LivenessEvaluator(mode=lv.LivenessMode.HEAVY)
    heavy_clean = [ev3.observe(clean_readings()) for _ in range(6)]
    check("heavy mode stays pending with no confirm cue",
          heavy_clean[-1].decision is lv.Decision.PENDING, heavy_clean[-1].decision.value)

    ev3b = lv.LivenessEvaluator(mode=lv.LivenessMode.HEAVY)
    motion = clean_readings()
    motion[lv.Cue.MICRO_MOTION] = lv.CueReading(1.0, 1.0)
    heavy_out = None
    for _ in range(3):
        heavy_out = ev3b.observe(motion)
    check("heavy mode refuses movement alone",
          heavy_out.decision is lv.Decision.PENDING, heavy_out.decision.value)

    ev3c = lv.LivenessEvaluator(mode=lv.LivenessMode.STRICT)
    strict_out = None
    for _ in range(6):
        strict_out = ev3c.observe(motion)
    check("strict mode refuses movement alone",
          strict_out.decision is lv.Decision.PENDING, strict_out.decision.value)
    head_turn = clean_readings()
    head_turn[lv.Cue.HEAD_TURN] = lv.CueReading(1.0, 1.0)
    check("strict mode accepts the head-turn challenge",
          ev3c.observe(head_turn).decision is lv.Decision.CONFIRMED)

    check("unknown mode falls back to heavy, never off",
          lv.LivenessMode.from_name("off") is lv.LivenessMode.HEAVY)

    ev4 = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    abstain = clean_readings()
    abstain[lv.Cue.GLOSS_GLARE] = lv.CueReading(1.0, 0.0)  # confident level, zero confidence
    for _ in range(5):
        out4 = ev4.observe(abstain)
    check("confidence 0 abstains rather than convicts", out4.decision is lv.Decision.CONFIRMED,
          out4.decision.value)

    ev5 = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    one_deny = clean_readings()
    one_deny[lv.Cue.GLOSS_GLARE] = lv.CueReading(1.0, 1.0)
    # gloss needs 3 counted frames before it fires, so feed 3, then let it drop out
    for _ in range(3):
        ev5.observe(one_deny)
    for _ in range(4):
        out5 = ev5.observe(clean_readings())
    check("firing latches across dropouts", out5.decision is lv.Decision.DENIED,
          out5.decision.value)

    # ------------------------------------------------------------------ report
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
