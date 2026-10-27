"""Check an existing enrollment: spot bad samples, then test against the live camera."""
from __future__ import annotations

import numpy as np

from glance_core import GlancePipeline

pipeline = GlancePipeline()
if not pipeline.store.identities:
    raise SystemExit("no identities enrolled")

identity = pipeline.store.identities[0]
S = np.stack(identity.samples)
print(f"identity '{identity.name}': {len(S)} samples\n")

# pairwise similarity matrix
M = S @ S.T
print("pairwise similarity against each sample:")
header = "        " + "".join(f"s{j:<6d}" for j in range(len(S)))
print(header)
for i in range(len(S)):
    row = "".join(f"{M[i, j]:6.3f} " for j in range(len(S)))
    mean_others = (M[i].sum() - 1.0) / (len(S) - 1)
    flag = "  <-- weak" if mean_others < 0.75 else ""
    print(f"  s{i}  {row} avg={mean_others:.3f}{flag}")

print("\nworst pair:", f"{M[np.triu_indices(len(S), 1)].min():.3f}")

# live capture test
print("\ncapturing 10 live frames (look at the camera)...")
cap = pipeline.open_camera()
sims_centroid, sims_nearest = [], []
try:
    for i in range(10):
        ok, frame = cap.read()
        if not ok:
            break
        frame = np.flip(frame, 1)
        emb, face, _ = pipeline.embedding_for(frame)
        if emb is None:
            print(f"  frame {i + 1:02d}: no usable face")
            continue
        c, n = identity.score(emb)
        sims_centroid.append(c)
        sims_nearest.append(n)
        verdict = "MATCH" if (c >= pipeline.threshold and n >= pipeline.threshold) else "no match"
        print(f"  frame {i + 1:02d}: centroid {c:.3f}  nearest {n:.3f}  -> {verdict}")
finally:
    cap.release()

if sims_centroid:
    print(f"\ncentroid: mean {np.mean(sims_centroid):.3f} min {np.min(sims_centroid):.3f}")
    print(f"nearest : mean {np.mean(sims_nearest):.3f} min {np.min(sims_nearest):.3f}")
    passed = sum(1 for c, n in zip(sims_centroid, sims_nearest)
                 if c >= pipeline.threshold and n >= pipeline.threshold)
    print(f"threshold {pipeline.threshold:.2f}: {passed}/{len(sims_centroid)} frames would unlock")
