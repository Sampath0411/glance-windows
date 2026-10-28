import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from glance.core import FaceDetector

ROOT = Path(__file__).resolve().parents[2]
M = ROOT / "models" / "buffalo_s"
out = ROOT / "data" / "selftest"
SIZE = 192

sess = ort.InferenceSession(str(M / "2d106det.onnx"), providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0].name

frame = cv2.imread(str(out / "frame.png"))
face = FaceDetector().detect(frame)[0]
x1, y1, x2, y2 = face.bbox
w, h = x2 - x1, y2 - y1
cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
print(f"face bbox w={w:.0f} h={h:.0f}   SCRFD kps:\n{face.kps.round(0)}")

# insightface convention: square crop, side = max(w,h)*1.5, centered on bbox center
side = max(w, h) * 1.5
m = np.array([[SIZE / side, 0, SIZE / 2 - cx * SIZE / side],
              [0, SIZE / side, SIZE / 2 - cy * SIZE / side]], dtype=np.float32)
crop = cv2.warpAffine(frame, m, (SIZE, SIZE), flags=cv2.INTER_LINEAR, borderValue=0)
cv2.imwrite(str(out / "lmk_crop_192.png"), crop)
rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)

for nname, blob in {
    "raw 0-255 (graph normalizes)": rgb,
    "(x-127.5)/128 (no graph norm)": (rgb - 127.5) / 128.0,
}.items():
    pred = sess.run(None, {inp: blob.transpose(2, 0, 1)[None]})[0][0].reshape(-1, 2).astype(np.float32)
    # insightface post-processing
    pred = (pred + 1) * (SIZE // 2)
    inv = cv2.invertAffineTransform(m)
    pts = cv2.transform(pred.reshape(1, -1, 2), inv).reshape(-1, 2)

    inside_crop = ((pred >= 0) & (pred <= SIZE)).all(axis=1).sum()
    span = pts.max(axis=0) - pts.min(axis=0)
    area_ratio = (span[0] * span[1]) / (w * h)
    print(f"\n{nname}")
    print(f"  in-crop {inside_crop}/106  span=({span[0]:.0f},{span[1]:.0f})  area/face={area_ratio:.2f}")

    vis = crop.copy()
    for i, (px, py) in enumerate(pred):
        if 0 <= px < SIZE and 0 <= py < SIZE:
            cv2.circle(vis, (int(px), int(py)), 1, (0, 255, 0), -1)
    cv2.imwrite(str(out / f"lmk_vis_{'raw' if 'raw' in nname else 'norm'}.png"),
                cv2.resize(vis, (576, 576), interpolation=cv2.INTER_NEAREST))
    np.save(out / f"lmk_{'raw' if 'raw' in nname else 'norm'}.npy", pts)
