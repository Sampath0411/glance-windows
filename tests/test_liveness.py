"""
Phase 2 liveness tests.

Coverage:
  1. false positives  - a real face frame must NOT trip either deny cue
  2. device bezel     - a synthetic phone screen around the face must trip it
  3. glare            - a synthetic flat specular blob must trip it
  4. evaluator        - deny overrides confirm; light mode needs 3 frames;
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


def main() -> int:
    frame = cv2.imread(str(FRAME))
    if frame is None:
        print(f"missing {FRAME}", file=sys.stderr)
        return 1
    face = FaceDetector().detect(frame)[0]
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    pad = int(0.15 * max(x2 - x1, y2 - y1))
    crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
    print(f"frame {frame.shape[1]}x{frame.shape[0]}, face bbox {face.bbox.round(0)}\n")

    # ---------------------------------------------------------------- 1. real
    print("1. real face, no spoof")
    glare = lv.extract_glare(crop)
    device = lv.detect_device_rectangle(frame, tuple(face.bbox))
    gloss_r = lv.gloss_glare_reading(glare)
    device_r = lv.device_reading(device)
    print(f"   glare:  fraction={glare.specular_fraction:.4f} cluster={glare.specular_cluster_ratio:.3f} "
          f"-> {describe(gloss_r)}")
    print(f"   device: rect={device.rectangle} overlap={device.face_overlap_fraction} "
          f"-> {describe(device_r)}")
    check("real face does not trip glare", gloss_r.level < lv.LivenessTuning().gloss_level,
          describe(gloss_r))
    check("real face does not trip device", (device_r.confidence == 0) or
          (device_r.level < lv.LivenessTuning().device_level), describe(device_r))

    # ------------------------------------------------------------- 2. device
    print("\n2. synthetic phone held up (emissive screen displaying the captured photo)")
    h, w = frame.shape[:2]
    spoof = frame.copy()
    px1, py1 = max(0, x1 - 90), max(0, y1 - 70)
    px2, py2 = min(w - 1, x2 + 90), min(h - 1, y2 + 120)

    # A phone showing a photo: emissive bright screen, the face inside it, dark bezel.
    cv2.rectangle(spoof, (px1, py1), (px2, py2), (250, 250, 250), -1)
    inset = 26
    screen = frame[py1 + inset:py2 - inset, px1 + inset:px2 - inset]
    if screen.size:
        screen = cv2.resize(screen, (px2 - px1 - 2 * inset, py2 - py1 - 2 * inset))
        screen = cv2.convertScaleAbs(screen, alpha=1.05, beta=18)  # backlit screen
        spoof[py1 + inset:py2 - inset, px1 + inset:px2 - inset] = screen
    cv2.rectangle(spoof, (px1, py1), (px2, py2), (18, 18, 18), 16)  # bezel
    cv2.imwrite(str(DATA / "selftest" / "spoof_device.png"), spoof)

    spoof_face = FaceDetector().detect(spoof)
    box = tuple(spoof_face[0].bbox) if spoof_face else tuple(face.bbox)
    d2 = lv.detect_device_rectangle(spoof, box)
    r2 = lv.device_reading(d2)
    print(f"   rect={d2.rectangle} overlap={d2.face_overlap_fraction} -> {describe(r2)}")
    check("device bezel detected on spoof", r2.level >= lv.LivenessTuning().device_level,
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
    check("glare detected on spoof", r3.level >= lv.LivenessTuning().gloss_level, describe(r3))

    # ---------------------------------------------------------- 4. evaluator
    print("\n4. evaluator semantics")
    ev = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    clean = {c: lv.CueReading.none() for c in lv.Cue}
    snaps = [ev.observe(clean) for _ in range(3)]
    check("light mode pending before minimum frames", snaps[0].decision is lv.Decision.PENDING,
          snaps[0].decision.value)
    check("light mode confirms after 3 clean frames", snaps[2].decision is lv.Decision.CONFIRMED,
          snaps[2].decision.value)

    ev2 = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    deny = dict(clean)
    deny[lv.Cue.DEVICE_DETECTED] = lv.CueReading(0.9, 1.0)
    out = None
    for _ in range(3):
        out = ev2.observe(deny)
    check("deny overrides confirm", out.decision is lv.Decision.DENIED, out.denied_by.value if out.denied_by else "")
    check("denial reason is user-facing", bool(out.denial_reason()), out.denial_reason() or "")

    ev3 = lv.LivenessEvaluator(mode=lv.LivenessMode.HEAVY)
    heavy_clean = [ev3.observe(clean) for _ in range(6)]
    check("heavy mode stays pending with no confirm cue",
          heavy_clean[-1].decision is lv.Decision.PENDING, heavy_clean[-1].decision.value)

    ev4 = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    abstain = dict(clean)
    abstain[lv.Cue.GLOSS_GLARE] = lv.CueReading(1.0, 0.0)  # confident level, zero confidence
    for _ in range(5):
        out4 = ev4.observe(abstain)
    check("confidence 0 abstains rather than convicts", out4.decision is lv.Decision.CONFIRMED,
          out4.decision.value)

    ev5 = lv.LivenessEvaluator(mode=lv.LivenessMode.LIGHT)
    one_deny = dict(clean)
    one_deny[lv.Cue.GLOSS_GLARE] = lv.CueReading(1.0, 1.0)
    # gloss needs 3 counted frames before it fires, so feed 3, then let it drop out
    for _ in range(3):
        ev5.observe(one_deny)
    for _ in range(4):
        out5 = ev5.observe(clean)
    check("firing latches across dropouts", out5.decision is lv.Decision.DENIED,
          out5.decision.value)

    # ------------------------------------------------------------------ report
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
