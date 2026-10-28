"""
Recognition sanity check - does the ported pipeline actually work?

Captures faces over several seconds, then measures:
  1. SAME person across time  : split the captures into enroll/test halves
  2. DIFFERENT person         : similarity between the two largest faces when
                                more than one is on camera

Writes nothing permanent. Reports the similarity distributions so the threshold
can be chosen from evidence instead of copying 0.6 blindly.
"""

from __future__ import annotations

import time

import numpy as np

from ..core import GlancePipeline, cosine
from ..paths import PATHS

OUT = PATHS.data_dir / "eval"


def main(frames: int = 24) -> int:
    pipeline = GlancePipeline()
    cap = pipeline.open_camera()
    OUT.mkdir(parents=True, exist_ok=True)

    per_frame: list[list[tuple[np.ndarray, float]]] = []
    print(f"Capturing {frames} frames. Sit in front of the camera, move a little.\n")
    try:
        for i in range(frames):
            ok, frame = cap.read()
            if not ok:
                print("camera read failed")
                return 1
            frame = np.flip(frame, 1)
            faces = pipeline.detector.detect(frame)
            embs = []
            for face in faces[:2]:  # largest two
                aligned = pipeline.aligner.align(frame, face)
                if aligned is None:
                    continue
                embs.append((pipeline.embedder.embed(aligned), face.score))
            per_frame.append(embs)
            print(f"  frame {i + 1:02d}: {len(faces)} face(s) detected, {len(embs)} embedded")
            time.sleep(0.12)
    finally:
        cap.release()

    # Flatten to the primary (largest) face per frame - that's the person at the camera.
    primary = [f[0][0] for f in per_frame if f]
    if len(primary) < 6:
        print(f"\nOnly {len(primary)} usable faces; need 6+. Better lighting / closer please.")
        return 1

    half = len(primary) // 2
    enroll, test = primary[:half], primary[half:]

    def stats(vals: list[float]) -> tuple[float, float, float]:
        return float(np.mean(vals)), float(np.min(vals)), float(np.max(vals))

    # 1. same person, enroll set vs test set (cross-time generalisation)
    cross = [cosine(e, t) for e in enroll for t in test]
    # 2. same person, within the enroll set only (upper bound / self-similarity)
    within = [cosine(enroll[i], enroll[j]) for i in range(len(enroll)) for j in range(i + 1, len(enroll))]
    # 3. different person, when both were visible in the same frame
    cross_person = []
    for f in per_frame:
        if len(f) >= 2:
            cross_person.append(cosine(f[0][0], f[1][0]))

    mean_c, min_c, max_c = stats(cross)
    mean_w, min_w, _ = stats(within)
    print("\n--- same person ---")
    print(f"  within enroll set : mean {mean_w:.3f}  min {min_w:.3f}")
    print(f"  enroll vs test    : mean {mean_c:.3f}  min {min_c:.3f}  max {max_c:.3f}")

    if cross_person:
        mean_d, min_d, max_d = stats(cross_person)
        print("\n--- different person (two faces in frame) ---")
        print(f"  similarity        : mean {mean_d:.3f}  min {min_d:.3f}  max {max_d:.3f}")
        print(f"  separation        : {mean_w - mean_d:+.3f} (want clearly positive)")
        verdict = "SEPARABLE" if min_w > max_d else "OVERLAP - check lighting/poses"
        print(f"  verdict           : {verdict}")
    else:
        print("\n--- different person ---")
        print("  only one face was ever detected; have someone else sit down to test this")

    print("\n--- threshold guidance ---")
    print(f"  a threshold between {min_c:.2f} and {mean_c:.2f} would accept your test frames")
    print(f"  Glance defaults to 0.60; Phase 1 code uses the same value")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
