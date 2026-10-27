import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "models" / "buffalo_s"
out = ROOT / "data" / "selftest"

s = ort.InferenceSession(str(M / "2d106det.onnx"), providers=["CPUExecutionProvider"])
inp = s.get_inputs()[0].name
aligned = cv2.imread(str(out / "aligned_112.png"))
big = cv2.resize(aligned, (192, 192), interpolation=cv2.INTER_LINEAR)
rgb = cv2.cvtColor(big, cv2.COLOR_BGR2RGB).astype(np.float32)
bgr = big.astype(np.float32)

variants = {
    "rgb_(x-127.5)/128": ((rgb - 127.5) / 128.0).transpose(2, 0, 1)[None],
    "rgb_(x-127.5)/127.5": ((rgb - 127.5) / 127.5).transpose(2, 0, 1)[None],
    "rgb_x/255": (rgb / 255.0).transpose(2, 0, 1)[None],
    "rgb_raw": rgb.transpose(2, 0, 1)[None],
    "bgr_(x-127.5)/128": ((bgr - 127.5) / 128.0).transpose(2, 0, 1)[None],
    "bgr_raw": bgr.transpose(2, 0, 1)[None],
}

for name, blob in variants.items():
    pts = s.run(None, {inp: blob.astype(np.float32)})[0].reshape(106, 2)
    inside = ((pts >= 0) & (pts <= 192)).all(axis=1).sum()
    span = pts.max(axis=0) - pts.min(axis=0)
    # a plausible face: vertically taller than wide, mostly inside the crop
    plausible = inside >= 100 and 0.6 < span[0] / max(span[1], 1e-6) < 1.6
    print(f"{name:22s} inside={inside:3d}/106 span={span.round(1)} "
          f"x=[{pts[:,0].min():.0f},{pts[:,0].max():.0f}] y=[{pts[:,1].min():.0f},{pts[:,1].max():.0f}]"
          f"{'   <-- plausible' if plausible else ''}")
