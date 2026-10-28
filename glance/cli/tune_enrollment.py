r"""
Diagnose and repair enrollment quality.

Problem this solves: a few bad captures (blur, extreme angle, motion) pull the
centroid template away from what the camera actually sees, so a live frame scores
0.3-0.5 against its own enrollment and never clears the 0.6 threshold.

Method: capture two disjoint frame sets. The first may be offered as extra samples,
the second is held out and used only for scoring, so adding frames cannot
self-certify. Every candidate template is scored on the same held-out set.

  .venv\Scripts\python.exe -m glance.cli.tune_enrollment            # measure only
  .venv\Scripts\python.exe -m glance.cli.tune_enrollment --apply    # write the best one
"""

from __future__ import annotations

import argparse
import shutil

import cv2
import numpy as np

from ..core import DEFAULT_THRESHOLD, GlancePipeline, l2_normalize


def capture(pipeline: GlancePipeline, want: int) -> list[np.ndarray]:
    cap = pipeline.open_camera()
    embs: list[np.ndarray] = []
    try:
        for _ in range(want * 6):
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            emb, face, _ = pipeline.embedding_for(frame)
            if emb is None:
                continue
            embs.append(emb)
            if len(embs) >= want:
                break
    finally:
        cap.release()
    return embs


def evaluate(samples: list[np.ndarray], heldout: list[np.ndarray],
             threshold: float) -> dict:
    S = np.stack(samples)
    H = np.stack(heldout)
    template = l2_normalize(S.mean(axis=0))
    centroid = H @ template
    nearest = (H @ S.T).max(axis=1)
    passed = int(((centroid >= threshold) & (nearest >= threshold)).sum())
    return {
        "n_samples": len(samples),
        "centroid_mean": float(centroid.mean()),
        "centroid_min": float(centroid.min()),
        "nearest_mean": float(nearest.mean()),
        "nearest_min": float(nearest.min()),
        "passed": passed,
        "total": len(heldout),
    }


def coherent_subset(samples: list[np.ndarray], floor: float) -> list[np.ndarray]:
    """Keep samples that agree with the group; drop captures the camera disagrees with."""
    if len(samples) < 3:
        return list(samples)
    S = np.stack(samples)
    M = S @ S.T
    np.fill_diagonal(M, np.nan)
    avg = np.nanmean(M, axis=1)
    keep = [i for i in range(len(samples)) if avg[i] >= floor]
    return [samples[i] for i in keep] if keep else list(samples)


def report(name: str, result: dict) -> None:
    print(f"  {name:<26} samples={result['n_samples']:2d}  "
          f"centroid {result['centroid_mean']:.3f}/{result['centroid_min']:.3f}  "
          f"nearest {result['nearest_mean']:.3f}/{result['nearest_min']:.3f}  "
          f"pass {result['passed']}/{result['total']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the best template back")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--frames", type=int, default=8, help="held-out frames")
    args = ap.parse_args()

    pipeline = GlancePipeline()
    if not pipeline.store.identities:
        raise SystemExit("no identities enrolled")
    identity = pipeline.store.identities[0]
    enrolled = [np.asarray(s, dtype=np.float32) for s in identity.samples]

    S = np.stack(enrolled)
    M = S @ S.T
    np.fill_diagonal(M, np.nan)
    avg = np.nanmean(M, axis=1)
    print(f"identity '{identity.name}': {len(enrolled)} enrolled samples")
    for i, a in enumerate(avg):
        print(f"  s{i}: avg similarity to the others {a:.3f}"
              + ("   <- outlier" if a < 0.70 else ""))

    print(f"\ncapturing {args.frames} held-out frames (look at the camera)...")
    heldout = capture(pipeline, args.frames)
    if len(heldout) < 3:
        raise SystemExit(f"only captured {len(heldout)} usable frames")
    print(f"  got {len(heldout)}\n")

    print(f"scoring against threshold {args.threshold:.2f} "
          f"(both centroid and nearest must clear it):")
    all_samples = evaluate(enrolled, heldout, args.threshold)
    report("current (all samples)", all_samples)

    pruned = coherent_subset(enrolled, 0.70)
    pruned_result = evaluate(pruned, heldout, args.threshold)
    report("pruned (coherent only)", pruned_result)

    fresh = capture(pipeline, 5)
    print(f"\n  captured {len(fresh)} extra frames as candidate samples")
    augmented = evaluate(enrolled + fresh, heldout, args.threshold)
    report("current + 5 fresh", augmented)

    both = evaluate(pruned + fresh, heldout, args.threshold)
    report("pruned + 5 fresh", both)

    candidates = [
        ("current (all samples)", enrolled, all_samples),
        ("pruned (coherent only)", pruned, pruned_result),
        ("current + 5 fresh", enrolled + fresh, augmented),
        ("pruned + 5 fresh", pruned + fresh, both),
    ]
    best_name, best_samples, best = max(
        candidates, key=lambda c: (c[2]["passed"], c[2]["centroid_min"]))

    print(f"\nbest: {best_name}  ({best['passed']}/{best['total']} frames would unlock)")

    if best["passed"] < best["total"]:
        print("  NOTE: still not clearing every frame - more varied samples in the "
              "same lighting usually fixes this.")

    if args.apply and best_samples is not enrolled:
        backup = pipeline.store.path.with_suffix(".json.bak")
        shutil.copy2(pipeline.store.path, backup)
        identity.samples = [np.asarray(s, dtype=np.float32) for s in best_samples]
        pipeline.store.save()
        print(f"\napplied. backup at {backup.name}")
    elif args.apply:
        print("\ncurrent template is already the best; nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
