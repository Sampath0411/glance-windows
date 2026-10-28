"""Localise a bad match: are live frames consistent with EACH OTHER, or with the enrollment?"""
from __future__ import annotations

import cv2
import numpy as np

from ..core import GlancePipeline, cosine

pipeline = GlancePipeline()
identity = pipeline.store.identities[0]

cap = pipeline.open_camera()
embs, frames = [], []
try:
    for _ in range(8):
        ok, frame = cap.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        emb, face, aligned = pipeline.embedding_for(frame)
        if emb is None:
            continue
        embs.append(emb)
        frames.append((frame.copy(), face, aligned))
finally:
    cap.release()

print(f"captured {len(embs)} usable frames\n")

if len(embs) >= 2:
    live_m = np.stack(embs) @ np.stack(embs).T
    off = live_m[np.triu_indices(len(embs), 1)]
    print("live frame vs live frame:")
    print(f"  mean {off.mean():.3f}  min {off.min():.3f}  max {off.max():.3f}")

enrolled = np.stack(identity.samples)
cross = np.stack(embs) @ enrolled.T
print("\nlive frame vs enrolled samples:")
print(f"  mean {cross.mean():.3f}  max {cross.max():.3f}")

print("\nverdict:")
if len(embs) >= 2 and off.mean() > 0.8 and cross.max() < 0.5:
    print("  live frames agree with each other but not with the enrollment.")
    print("  -> the enrollment is of a different subject or was captured badly.")
elif len(embs) >= 2 and off.mean() < 0.7:
    print("  live frames disagree with each other -> unstable pipeline or bad frames.")
else:
    print("  inconclusive")

# save evidence so the frame can actually be looked at
if frames:
    frame, face, aligned = frames[-1]
    dbg = frame.copy()
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    cv2.rectangle(dbg, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.imwrite("data/selftest/live_now.png", dbg)
    if aligned is not None:
        cv2.imwrite("data/selftest/live_now_aligned.png",
                    cv2.resize(aligned, (336, 336), interpolation=cv2.INTER_NEAREST))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    print(f"\nframe stats: mean brightness {gray.mean():.1f}/255, "
          f"std {gray.std():.1f}, face box area {face.area:.0f} px")
    print("wrote data/selftest/live_now.png and live_now_aligned.png")
