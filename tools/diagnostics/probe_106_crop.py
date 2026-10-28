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
print(f"face bbox {face.bbox.round(0)}  w={w:.0f} h={h:.0f}")

results = []
for k in (1.2, 1.5, 1.8, 2.0, 2.4, 2.8):
    side = max(w, h) * k
    m = np.array([[SIZE / side, 0, -(cx - side / 2) * SIZE / side],
                  [0, SIZE / side, -(cy - side / 2) * SIZE / side]], dtype=np.float32)
    crop = cv2.warpAffine(frame, m, (SIZE, SIZE), flags=cv2.INTER_LINEAR, borderValue=0)
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)

    for nname, blob in {
        "(x-127.5)/128": (rgb - 127.5) / 128.0,
        "raw": rgb,
        "/255": rgb / 255.0,
    }.items():
        pts_local = sess.run(None, {inp: blob.transpose(2, 0, 1)[None]})[0].reshape(106, 2) * SIZE
        inside = ((pts_local >= 0) & (pts_local <= SIZE)).all(axis=1).sum()
        # map back to frame space
        inv = cv2.invertAffineTransform(m)
        ones = np.ones((106, 1), np.float32)
        pts_img = (np.hstack([pts_local, ones]) @ inv.T)
        span_img = pts_img.max(axis=0) - pts_img.min(axis=0)
        # a good fit: landmarks ~cover the face box, and land roughly inside the crop
        ratio = (span_img[0] * span_img[1]) / max(w * h, 1)
        results.append((k, nname, inside, ratio, span_img))

for k, nname, inside, ratio, span in sorted(results, key=lambda r: -r[2])[:10]:
    print(f"k={k:<4} {nname:<14} inside={inside:3d}/106  lm_area/face_area={ratio:5.2f}  "
          f"span=({span[0]:.0f},{span[1]:.0f})")
