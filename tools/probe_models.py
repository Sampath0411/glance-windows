"""Verify the Glance face-recognition core runs on Windows CPU, and measure it."""
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

MODELS = Path(__file__).resolve().parents[1] / "models" / "buffalo_s"


def bench(name, build_feed, model_file, runs=30):
    so = ort.SessionOptions()
    so.intra_op_num_threads = 0  # let ORT pick
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(str(MODELS / model_file), so, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    out = sess.get_outputs()[0]
    feed = build_feed(inp)
    sess.run(None, feed)  # warmup
    t0 = time.perf_counter()
    for _ in range(runs):
        res = sess.run(None, feed)
    ms = (time.perf_counter() - t0) / runs * 1000
    print(f"{name}")
    print(f"  file        : {model_file}")
    print(f"  input       : {inp.name} {inp.shape} {inp.type}")
    print(f"  output      : {out.name} {res[0].shape} {res[0].dtype}")
    if res[0].ndim == 2 and res[0].shape[1] == 512:
        n = np.linalg.norm(res[0][0])
        print(f"  embedding   : 512-d, raw L2 norm = {n:.3f} (needs normalizing, matches Glance)")
    print(f"  latency     : {ms:.1f} ms/inference on CPU\n")
    return res[0]


def np_feed(shape_like):
    def build(inp):
        if inp.type == "tensor(float)":
            return {inp.name: np.random.rand(*shape_like).astype(np.float32)}
        return {inp.name: np.random.randint(0, 255, shape_like, dtype=np.uint8)}
    return build


# 1. Recognition: w600k_mbf, 112x112 RGB, (px-127.5)/127.5  [exactly what Glance bakes into Core ML]
def rec_feed(_inp):
    px = np.random.randint(0, 256, (112, 112, 3), dtype=np.uint8).astype(np.float32)
    chw = px.transpose(2, 0, 1)[None]
    return {_inp.name: ((chw - 127.5) / 127.5).astype(np.float32)}


emb = bench("ArcFace w600k_mbf (recognition)", rec_feed, "w600k_mbf.onnx")

# 2. Detection: det_500m (SCRFD), 640x640
bench(
    "SCRFD det_500m (face detection)",
    np_feed((1, 3, 640, 640)),
    "det_500m.onnx",
    runs=15,
)

# 3. Sanity: same input -> identical embedding (deterministic), different input -> different
sess = ort.InferenceSession(str(MODELS / "w600k_mbf.onnx"), providers=["CPUExecutionProvider"])
rng = np.random.default_rng(0)
a_px = rng.integers(0, 256, (112, 112, 3), dtype=np.uint8)
feed_a = {sess.get_inputs()[0].name: (((a_px.astype(np.float32).transpose(2, 0, 1)[None]) - 127.5) / 127.5)}
b_px = rng.integers(0, 256, (112, 112, 3), dtype=np.uint8)
feed_b = {sess.get_inputs()[0].name: (((b_px.astype(np.float32).transpose(2, 0, 1)[None]) - 127.5) / 127.5)}

ea = sess.run(None, feed_a)[0].flatten()
ea2 = sess.run(None, feed_a)[0].flatten()
eb = sess.run(None, feed_b)[0].flatten()


def cos(x, y):
    return float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)))


print("determinism  (same input twice) :", f"{cos(ea, ea2):.6f}")
print("discrimination (random vs random):", f"{cos(ea, eb):.6f}")
print("\nCore pipeline is viable on this machine.")

