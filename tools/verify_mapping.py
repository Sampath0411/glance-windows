import numpy as np
from pathlib import Path

out = Path(__file__).resolve().parents[1] / "data" / "selftest"
pts = np.load(out / "lmk_raw.npy")

# Standard InsightFace 2d106 layout (33+10+9+5+8+10+10+11+10 = 106)
RANGES = {
    "contour":   (0, 33),
    "brow_r":    (33, 43),
    "brow_l":    (43, 52),
    "nose_bridge": (52, 57),
    "nose":      (57, 65),
    "eye_r":     (65, 75),
    "eye_l":     (75, 85),
    "lip_outer": (85, 96),
    "lip_inner": (96, 106),
}

print(f"{'region':12s} {'n':>3s} {'cx':>7s} {'cy':>7s} {'w':>6s} {'h':>6s}")
for name, (a, b) in RANGES.items():
    p = pts[a:b]
    print(f"{name:12s} {len(p):3d} {p[:,0].mean():7.1f} {p[:,1].mean():7.1f} "
          f"{np.ptp(p[:,0]):6.1f} {np.ptp(p[:,1]):6.1f}")

# Sanity assertions a correct mapping must satisfy
cx = pts[:, 0].mean()
checks = []
checks.append(("eyes left of / right of centre",
               pts[75:85, 0].mean() < cx < pts[65:75, 0].mean()))
checks.append(("nose below eyes", pts[57:65, 1].mean() > pts[65:85, 1].mean()))
checks.append(("lips below nose", pts[85:96, 1].mean() > pts[57:65, 1].mean()))
checks.append(("brows above eyes", pts[33:52, 1].mean() < pts[65:85, 1].mean()))
checks.append(("inner lip inside outer lip",
               np.ptp(pts[96:106, 0]) < np.ptp(pts[85:96, 0])))
checks.append(("contour widest", np.ptp(pts[0:33, 0]) >= max(
    np.ptp(pts[a:b, 0]) for a, b in RANGES.values() if (a, b) != (0, 33))))

print()
ok = True
for label, passed in checks:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}")
    ok &= bool(passed)
print("\nmapping is", "consistent" if ok else "WRONG - do not build cues on this")

# eye centre + EAR reference numbers, useful for later tuning
r_eye, l_eye = pts[65:75], pts[75:85]
print(f"\nright eye centre {(r_eye[:,0].mean()):.1f},{r_eye[:,1].mean():.1f}")
print(f"left  eye centre {(l_eye[:,0].mean()):.1f},{l_eye[:,1].mean():.1f}")
iod = np.linalg.norm(r_eye.mean(axis=0) - l_eye.mean(axis=0))
print(f"interocular distance {iod:.1f} px  ({iod / (pts[:,0].ptp()):.3f} of face width)")

