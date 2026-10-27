import onnxruntime as ort
import numpy as np
from pathlib import Path

M = Path(__file__).resolve().parents[1] / "models" / "buffalo_s"

for f in ["det_500m.onnx", "w600k_mbf.onnx"]:
    s = ort.InferenceSession(str(M / f), providers=["CPUExecutionProvider"])
    print(f"=== {f} ===")
    print(" inputs:")
    for i in s.get_inputs():
        print(f"   {i.name:24s} {i.shape} {i.type}")
    print(" outputs:")
    inp = s.get_inputs()[0]
    shape = [d if isinstance(d, int) else (640 if f.startswith("det") else 112) for d in inp.shape]
    feed = {inp.name: np.random.rand(*shape).astype(np.float32)}
    res = s.run(None, feed)
    for o, r in zip(s.get_outputs(), res):
        print(f"   {o.name:24s} {str(r.shape):18s} {r.dtype}")
    print()
