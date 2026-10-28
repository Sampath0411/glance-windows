"""
Glance for Windows - Phase 1 app.

  python -m glance.cli.app enroll --name <you>     capture your face, build the template
  python -m glance.cli.app live                    continuous match with live scores
  python -m glance.cli.app selftest                headless: camera + full pipeline, no window

Phase 1 does NOT unlock anything. It only proves the recognition stack works on
this machine. The credential provider that actually unlocks Windows is Phase 3.
"""

from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np

from ..core import (
    DEFAULT_THRESHOLD,
    MIN_FACE_FRACTION,
    GlancePipeline,
    draw_banner,
    draw_face,
    fps_meter,
)
from .. import liveness as lv
from ..paths import PATHS

DATA_DIR = PATHS.data_dir


def guide_text(face) -> str:
    if face is None:
        return "No face - move closer, face the camera"
    x1, y1, x2, y2 = face.bbox
    if face.area < 40_000:
        return "Move closer"
    if face.area > 400_000:
        return "Move back a little"
    return "Hold still"


def cmd_enroll(args) -> int:
    pipeline = GlancePipeline(DATA_DIR / "identities.json", args.threshold)
    cap = pipeline.open_camera(args.camera)
    print(f"Enrolling '{args.name}'. Look at the camera and slowly turn your head.")
    print("SPACE = capture a sample, Q = finish and save, ESC = cancel.\n")

    samples: list[np.ndarray] = []
    meter = fps_meter()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("camera read failed", file=sys.stderr)
                return 1
            frame = cv2.flip(frame, 1)  # mirror, like a mirror
            fps = meter.tick()

            emb, face, _ = pipeline.embedding_for(frame)
            if face is not None:
                draw_face(frame, face)
            draw_banner(frame, f"ENROLL {args.name}  samples={len(samples)}  {fps:4.1f}fps", (120, 220, 255))
            draw_banner(frame, guide_text(face), (200, 200, 200), y=80)
            if emb is None:
                draw_banner(frame, "waiting for a face...", (90, 90, 220), y=120)

            cv2.imshow("Glance - enroll", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == 27:
                print("cancelled, nothing saved")
                return 1
            if key in (ord("q"), 13):
                break
            if key == 32 and emb is not None:
                # reject near-duplicate frames so the template isn't 15 copies of one pose
                if samples and max(float(np.dot(emb, s)) for s in samples) > 0.97:
                    print("  skipped: too similar to an existing sample, change angle/lighting")
                else:
                    samples.append(emb)
                    print(f"  sample {len(samples)} captured")
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if len(samples) < 3:
        print(f"need at least 3 samples, got {len(samples)}. Nothing saved.", file=sys.stderr)
        return 1

    pipeline.store.identities = [i for i in pipeline.store.identities if i.name != args.name]
    for emb in samples:
        pipeline.store.add_sample(args.name, emb)
    pipeline.store.save()

    quality = _quality(samples)
    print(f"\nSaved {len(samples)} samples for '{args.name}' -> {pipeline.store.path}")
    print(f"Self-similarity (centroid vs samples): mean {quality[0]:.3f}, worst {quality[1]:.3f}")
    print(f"Threshold is {pipeline.threshold:.2f}. Run: python -m glance.cli.app live")
    return 0


def _quality(samples: list[np.ndarray]) -> tuple[float, float]:
    centroid = np.mean(np.stack(samples), axis=0)
    centroid /= np.linalg.norm(centroid)
    sims = [float(np.dot(centroid, s)) for s in samples]
    return float(np.mean(sims)), float(np.min(sims))


def cmd_live(args) -> int:
    pipeline = GlancePipeline(DATA_DIR / "identities.json", args.threshold)
    if not pipeline.store.identities:
        print("No enrolled identities. Run: python -m glance.cli.app enroll --name <you>", file=sys.stderr)
        return 1

    mode = {"off": None, "light": lv.LivenessMode.LIGHT, "heavy": lv.LivenessMode.HEAVY}[args.liveness]
    evaluator = lv.LivenessEvaluator(mode=mode) if mode else None

    cap = pipeline.open_camera(args.camera)
    print(f"Live match. {len(pipeline.store.identities)} identity(ies) loaded, "
          f"threshold={pipeline.threshold:.2f}, liveness={args.liveness}. Q or ESC to quit.\n")

    meter = fps_meter()
    history: list[float] = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return 1
            frame = cv2.flip(frame, 1)
            fps = meter.tick()

            emb, face, _ = pipeline.embedding_for(frame)
            verdict, detail, colour = "NO FACE", "", (150, 150, 150)
            live_line, live_colour = "", (200, 200, 200)

            if emb is not None and face is not None:
                draw_face(frame, face)
                # A face this small cannot be identified: at 720p ~1.2% of the frame
                # is bbox side ~90px. Saying UNKNOWN here would blame the face when
                # the real problem is distance, and it also stops a background face
                # from being matched by accident.
                frame_h, frame_w = frame.shape[:2]
                face_fraction = face.area / float(frame_w * frame_h)
                match = None
                if face_fraction < MIN_FACE_FRACTION:
                    history.clear()
                    verdict = "TOO FAR - move closer"
                    detail = (f"face fills {face_fraction * 100:.1f}% of the frame; "
                              f"need {MIN_FACE_FRACTION * 100:.0f}% or more")
                    colour = (60, 200, 240)
                else:
                    match = pipeline.store.best_match(emb, pipeline.threshold)

                if match:
                    identity, centroid, nearest = match
                    history.append(centroid)
                    history = history[-5:]
                    stable = len(history) >= 3 and min(history) >= pipeline.threshold
                    verdict = f"MATCH {identity.name}" + ("  [stable]" if stable else "")
                    detail = f"centroid {centroid:.3f}  nearest {nearest:.3f}"
                    colour = (110, 230, 110)
                elif face_fraction >= MIN_FACE_FRACTION:
                    history.clear()
                    best_any = max(
                        (i.score(emb) for i in pipeline.store.identities if i.samples),
                        key=lambda x: x[0],
                        default=(0.0, 0.0),
                    )
                    verdict = "UNKNOWN"
                    detail = f"best centroid {best_any[0]:.3f}  (need {pipeline.threshold:.2f})"
                    colour = (90, 90, 230)

                if evaluator is not None and face_fraction >= MIN_FACE_FRACTION:
                    # Deny cues read the native-resolution face crop and the full
                    # frame; confirm cues read the running scan window.
                    x1, y1, x2, y2 = [int(v) for v in face.bbox]
                    pad = int(0.15 * max(x2 - x1, y2 - y1))
                    crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
                    glare = lv.extract_glare(crop)
                    device = lv.detect_device_rectangle(frame, tuple(face.bbox))
                    snap = evaluator.observe(lv.readings(glare, device, None))

                    if snap.decision is lv.Decision.DENIED:
                        live_line = f"LIVENESS: DENIED - {snap.denial_reason()}"
                        live_colour = (60, 60, 240)
                        colour = (60, 60, 240)
                        verdict = "SPOOF?"
                    elif snap.decision is lv.Decision.CONFIRMED:
                        by = f" ({snap.confirmed_by.title})" if snap.confirmed_by else ""
                        live_line = f"LIVENESS: pass [{evaluator.mode.value}]{by}"
                    else:
                        glare_state = snap.states[lv.Cue.GLOSS_GLARE]
                        device_state = snap.states[lv.Cue.DEVICE_DETECTED]
                        live_line = (f"LIVENESS: scanning  glare {glare_state.frames_counted}"
                                     f"/{evaluator.tuning.gloss_frames}  device "
                                     f"{device_state.frames_counted}/{evaluator.tuning.device_frames}")
            elif evaluator is not None:
                evaluator.reset()  # face lost: this is a new scan

            draw_banner(frame, f"{verdict}   {fps:4.1f}fps", colour)
            if detail:
                draw_banner(frame, detail, (210, 210, 210), y=80)
            if live_line:
                draw_banner(frame, live_line, live_colour, y=120)

            cv2.imshow("Glance - live", frame)
            if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


def cmd_bench(args) -> int:
    """Per-stage cost of the full pipeline on this machine, plus liveness outcome."""
    pipeline = GlancePipeline(DATA_DIR / "identities.json", args.threshold)
    evaluator = lv.LivenessEvaluator(mode=lv.LivenessMode(args.liveness if args.liveness != "off"
                                                         else "light"))
    cap = pipeline.open_camera(args.camera)
    timings = {"detect": [], "embed": [], "glare": [], "device": []}
    decisions: dict[str, int] = {}

    print(f"bench: {args.frames} frames, liveness={args.liveness}\n")
    try:
        for i in range(args.frames):
            ok, frame = cap.read()
            if not ok:
                return 1
            frame = cv2.flip(frame, 1)

            t0 = time.perf_counter()
            faces = pipeline.detector.detect(frame)
            timings["detect"].append((time.perf_counter() - t0) * 1000)
            if not faces:
                print(f"  frame {i + 1:02d}: no face")
                continue
            face = faces[0]

            t0 = time.perf_counter()
            aligned = pipeline.aligner.align(frame, face)
            emb = pipeline.embedder.embed(aligned) if aligned is not None else None
            timings["embed"].append((time.perf_counter() - t0) * 1000)

            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            pad = int(0.15 * max(x2 - x1, y2 - y1))
            crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]

            t0 = time.perf_counter()
            glare = lv.extract_glare(crop)
            timings["glare"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            device = lv.detect_device_rectangle(frame, tuple(face.bbox))
            timings["device"].append((time.perf_counter() - t0) * 1000)

            if args.liveness != "off":
                snap = evaluator.observe(lv.readings(glare, device, None))
                decisions[snap.decision.value] = decisions.get(snap.decision.value, 0) + 1
    finally:
        cap.release()

    def mean(xs):
        return f"{np.mean(xs):6.1f} ms" if xs else "     -  "

    print()
    for stage, xs in timings.items():
        print(f"  {stage:8s} {mean(xs)}  (n={len(xs)})")
    total = []
    for i in range(max(len(timings['detect']), len(timings['embed']))):
        s = timings['detect'][i] if i < len(timings['detect']) else 0
        s += timings['embed'][i] if i < len(timings['embed']) else 0
        s += timings['glare'][i] if i < len(timings['glare']) else 0
        s += timings['device'][i] if i < len(timings['device']) else 0
        total.append(s)
    if total:
        print(f"  {'TOTAL':8s} {np.mean(total):6.1f} ms  -> {1000 / np.mean(total):4.1f} fps")
    if decisions:
        print(f"\n  liveness decisions: {decisions}")
    return 0


def cmd_selftest(args) -> int:
    """No window. Proves camera -> detect -> align -> embed runs end to end here."""
    pipeline = GlancePipeline(DATA_DIR / "identities.json", args.threshold)
    cap = pipeline.open_camera(args.camera)
    print("selftest: grabbing frames (no window). Sit in front of the camera.\n")
    best = None
    try:
        for i in range(args.frames):
            ok, frame = cap.read()
            if not ok:
                print("camera read failed", file=sys.stderr)
                return 1
            frame = cv2.flip(frame, 1)
            t0 = time.perf_counter()
            emb, face, aligned = pipeline.embedding_for(frame)
            ms = (time.perf_counter() - t0) * 1000
            if face is None:
                print(f"  frame {i + 1:02d}: no face ({ms:.0f} ms)")
                continue
            print(f"  frame {i + 1:02d}: face det={face.score:.2f} bbox={face.bbox.round(0)} "
                  f"area={face.area:.0f} embed={'yes' if emb is not None else 'NO'} "
                  f"total={ms:.0f} ms")
            if emb is not None and (best is None or face.area > best[0]):
                best = (face.area, emb, frame.copy(), aligned)

        if best is None:
            print("\nNo usable face found. Check lighting and that the lens is uncovered.")
            return 1

        out = DATA_DIR / "selftest"
        out.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out / "frame.png"), best[2])
        cv2.imwrite(str(out / "aligned_112.png"), best[3])
        np.save(out / "embedding.npy", best[1])

        print(f"\nWrote {out / 'frame.png'}")
        print(f"Wrote {out / 'aligned_112.png'}  (112x112, this is what ArcFace sees)")
        print(f"Wrote {out / 'embedding.npy'}    (512-d, L2 norm {np.linalg.norm(best[1]):.4f})")

        store = pipeline.store
        if store.identities:
            match = store.best_match(best[1], pipeline.threshold)
            if match:
                print(f"\nMatches enrolled identity '{match[0].name}' "
                      f"(centroid {match[1]:.3f} >= {pipeline.threshold:.2f})")
            else:
                print(f"\nNo enrolled identity clears the {pipeline.threshold:.2f} threshold.")
        else:
            print("\nNo identities enrolled yet. Run: python -m glance.cli.app enroll --name <you>")
    finally:
        cap.release()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m glance.cli.app",
        description="Glance for Windows - Phase 1",
    )
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--liveness", choices=["off", "light", "heavy"], default="light",
                    help="light = deny cues only (Glance default), heavy = also require a confirm cue")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_scan_options(p: argparse.ArgumentParser) -> None:
        """Accept the scan options after the subcommand too.

        `python -m glance.cli.app --liveness heavy live` and `python -m glance.cli.app live --liveness heavy` should both
        work; the second form is what the launcher uses. default=SUPPRESS matters:
        without it the subparser's default would overwrite a value given before the
        subcommand.
        """
        p.add_argument("--threshold", type=float, default=argparse.SUPPRESS)
        p.add_argument("--camera", type=int, default=argparse.SUPPRESS)
        p.add_argument("--liveness", choices=["off", "light", "heavy"],
                       default=argparse.SUPPRESS)

    e = sub.add_parser("enroll", help="capture face samples")
    e.add_argument("--name", required=True)
    add_scan_options(e)
    e.set_defaults(func=cmd_enroll)

    l = sub.add_parser("live", help="continuous matching")
    add_scan_options(l)
    l.set_defaults(func=cmd_live)

    s = sub.add_parser("selftest", help="headless end-to-end check")
    s.add_argument("--frames", type=int, default=10)
    add_scan_options(s)
    s.set_defaults(func=cmd_selftest)

    b = sub.add_parser("bench", help="per-stage timing + liveness outcome")
    b.add_argument("--frames", type=int, default=20)
    add_scan_options(b)
    b.set_defaults(func=cmd_bench)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
