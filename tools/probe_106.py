import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "models" / "buffalo_s"
out = ROOT / "data" / "selftest"

s = ort.InferenceSession(str(M / "2d106det.onnx"), providers=["CPUExecutionProvider"])
print("inputs :", [(i.name, i.shape, i.type) for i in s.get_inputs()])
print("outputs:", [(o.name, o.shape, o.type) for o in s.get_outputs()])

inp = s.get_inputs()[0]
res = s.run(None, {inp.name: np.random.rand(1, 3, 192, 192).astype(np.float32)})
for o, r in zip(s.get_outputs(), res):
    print(f"  {o.name} -> {r.shape} (flat {r.size})")

# 106-point model expects the same 5-point alignment as recognition, at 192x192.
TEMPLATE_112 = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float32)

# reuse the aligned crop already written by selftest (112x112)
aligned = cv2.imread(str(out / "aligned_112.png"))
big = cv2.resize(aligned, (192, 192), interpolation=cv2.INTER_LINEAR)
rgb = cv2.cvtColor(big, cv2.COLOR_BGR2RGB).astype(np.float32)
res = s.run(None, {inp.name: ((rgb - 127.5) / 128.0).transpose(2, 0, 1)[None]})[0]
pts = res.reshape(-1, 2).reshape(106, 2) * 192.0
print("\nlandmark bbox:", pts.min(axis=0).round(1), pts.max(axis=0).round(1))

vis = big.copy()
for i, (x, y) in enumerate(pts):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < 192 and 0 <= y < 192:
        cv2.circle(vis, (x, y), 1, (0, 255, 0), -1)
for i, (x, y) in enumerate(pts):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < 192 and 0 <= y < 192:
        cv2.putText(vis, str(i), (x + 2, y - 2), cv2.FONT_HERSHEY_PLAIN, 0.45, (0, 200, 255), 1)

vis_big = cv2.resize(vis, (768, 768), interpolation=cv2.INTER_NEAREST)
cv2.imwrite(str(out / "landmarks_106.png"), vis_big)
print("wrote", out / "landmarks_106.png")
