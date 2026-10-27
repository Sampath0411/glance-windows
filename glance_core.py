"""
Glance for Windows - Phase 1 core.

A faithful port of the recognition half of github.com/jonnyoo/glance, which is
macOS-only (Swift + Vision + Core ML). The pieces that carry over exactly:

  * detector        : SCRFD det_500m (InsightFace buffalo_s)   -- replaces Vision face detection
  * alignment       : 5-point similarity transform onto the standard ArcFace
                      112x112 template, using the same coordinates as
                      glance/FaceAligner.swift
  * recognition     : ArcFace w600k_mbf, 512-d                 -- replaces Core ML ArcFace.mlpackage
  * preprocessing   : RGB, (px - 127.5) / 127.5               -- same scale/bias baked into
                      tools/convert_arcface.py
  * matching        : cosine similarity against a centroid template AND the
                      nearest individual sample; both must clear the threshold
                      -- same rule as FaceRecognitionPipeline.bestMatch

What deliberately does NOT carry over is the unlock mechanism. Glance types your
password into the macOS lock screen via CGEvent; Windows blocks synthetic input
to the secure desktop, so Phase 3 needs a credential provider instead.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from desktop.paths import PATHS

MODELS_DIR = PATHS.models_dir
DETECTOR_MODEL = MODELS_DIR / "det_500m.onnx"
RECOGNISER_MODEL = MODELS_DIR / "w600k_mbf.onnx"

# Standard ArcFace 112x112 template: left eye, right eye, nose, left mouth
# corner, right mouth corner. Identical to glance/FaceAligner.swift referencePoints.
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

EMBEDDING_DIM = 512
ALIGNED_SIZE = 112

# Glance's Face Lab default (FaceLabController.swift: var threshold: Double = 0.6).
# Two independent samples of the same face usually land around 0.5-0.8 with this
# model; 0.6 is fairly strict, which is the right default for unlocking.
DEFAULT_THRESHOLD = 0.6


# --------------------------------------------------------------------------- #
# math helpers
# --------------------------------------------------------------------------- #
def l2_normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
@dataclass
class DetectedFace:
    bbox: np.ndarray        # x1, y1, x2, y2 in source-image pixels
    score: float
    kps: np.ndarray         # 5x2 landmarks, source-image pixels

    @property
    def center(self) -> tuple[float, float]:
        return float((self.bbox[0] + self.bbox[2]) / 2), float((self.bbox[1] + self.bbox[3]) / 2)

    @property
    def area(self) -> float:
        return float(max(0.0, self.bbox[2] - self.bbox[0]) * max(0.0, self.bbox[3] - self.bbox[1]))


class FaceDetector:
    """SCRFD (Single-stage face detector with 5-point landmarks)."""

    _STRIDES = (8, 16, 32)
    _NUM_ANCHORS = 2

    def __init__(self, model_path: Path = DETECTOR_MODEL, det_size: int = 640,
                 score_threshold: float = 0.5, nms_threshold: float = 0.4):
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Two threads, not all four: the UI and the camera loop still need a core on a
        # 2C/4T laptop, and inference that starves the compositor reads as a frozen app.
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.det_size = det_size
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self._anchor_cache: dict[tuple[int, int, int], np.ndarray] = {}

    def _anchors(self, height: int, width: int, stride: int) -> np.ndarray:
        key = (height, width, stride)
        if key not in self._anchor_cache:
            grid = np.stack(np.mgrid[:height, :width][::-1], axis=-1).astype(np.float32)
            centers = (grid * stride).reshape(-1, 2)
            if self._NUM_ANCHORS > 1:
                centers = np.stack([centers] * self._NUM_ANCHORS, axis=1).reshape(-1, 2)
            self._anchor_cache[key] = centers
        return self._anchor_cache[key]

    @staticmethod
    def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
        x1 = points[:, 0] - distance[:, 0]
        y1 = points[:, 1] - distance[:, 1]
        x2 = points[:, 0] + distance[:, 2]
        y2 = points[:, 1] + distance[:, 3]
        return np.stack([x1, y1, x2, y2], axis=-1)

    @staticmethod
    def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
        out = []
        for i in range(0, distance.shape[1], 2):
            out.append(points[:, 0] + distance[:, i])
            out.append(points[:, 1] + distance[:, i + 1])
        return np.stack(out, axis=-1)

    def detect(self, image_bgr: np.ndarray) -> list[DetectedFace]:
        src_h, src_w = image_bgr.shape[:2]

        # Letterbox into det_size, preserving aspect ratio and padding top-left.
        # Stretching instead would distort faces and shift the anchors' meaning.
        ratio = src_h / src_w
        model_ratio = 1.0  # det_size is square
        if ratio > model_ratio:
            new_h = self.det_size
            new_w = int(new_h / ratio)
        else:
            new_w = self.det_size
            new_h = int(new_w * ratio)
        det_scale = new_h / src_h

        resized = cv2.resize(image_bgr, (new_w, new_h))
        canvas = np.zeros((self.det_size, self.det_size, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = resized

        # SCRFD preprocessing: RGB, NCHW, (px - 127.5) / 128.
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 128, (self.det_size, self.det_size), (127.5, 127.5, 127.5), swapRB=True
        )

        outs = self.session.run(self.output_names, {self.input_name: blob})
        scores_all, boxes_all, kps_all = outs[0:3], outs[3:6], outs[6:9]

        boxes, kpss, scores = [], [], []
        for idx, stride in enumerate(self._STRIDES):
            h, w = self.det_size // stride, self.det_size // stride
            anchors = self._anchors(h, w, stride)
            score = scores_all[idx].reshape(-1)
            bbox_pred = boxes_all[idx] * stride
            kps_pred = kps_all[idx] * stride

            keep = np.where(score >= self.score_threshold)[0]
            if keep.size == 0:
                continue
            boxes.append(self._distance2bbox(anchors[keep], bbox_pred[keep]))
            kpss.append(self._distance2kps(anchors[keep], kps_pred[keep]))
            scores.append(score[keep])

        if not boxes:
            return []

        boxes = np.vstack(boxes) / det_scale
        kpss = (np.vstack(kpss) / det_scale).reshape(-1, 5, 2)  # interleaved x,y -> (N,5,2)
        scores = np.concatenate(scores)

        idx = cv2.dnn.NMSBoxes(
            [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])] for b in boxes],
            scores.tolist(),
            self.score_threshold,
            self.nms_threshold,
        )
        faces = []
        for i in np.array(idx).reshape(-1):
            faces.append(DetectedFace(bbox=boxes[i], score=float(scores[i]), kps=kpss[i]))
        faces.sort(key=lambda f: f.area, reverse=True)
        return faces


# --------------------------------------------------------------------------- #
# alignment + embedding
# --------------------------------------------------------------------------- #
class FaceAligner:
    """Warps a face onto the canonical 112x112 pose ArcFace expects.

    Glance degrades gracefully through 5-point -> 2-point -> padded crop; we do
    the same so a partially occluded face still gets its best shot.
    """

    @staticmethod
    def align(image_bgr: np.ndarray, face: DetectedFace) -> np.ndarray:
        out = FaceAligner._warp(image_bgr, face.kps, ARCFACE_TEMPLATE)
        if out is not None:
            return out
        # Fallback: padded square crop, no rotation.
        x1, y1, x2, y2 = face.bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        half = max(x2 - x1, y2 - y1) * 0.7
        h, w = image_bgr.shape[:2]
        crop = image_bgr[
            int(max(0, cy - half)):int(min(h, cy + half)),
            int(max(0, cx - half)):int(min(w, cx + half)),
        ]
        if crop.size == 0:
            return None
        return cv2.resize(crop, (ALIGNED_SIZE, ALIGNED_SIZE), interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def _warp(image_bgr: np.ndarray, src_pts: np.ndarray, dst_pts: np.ndarray) -> np.ndarray | None:
        if src_pts is None or len(src_pts) < 2:
            return None
        matrix, _ = cv2.estimateAffinePartial2D(
            np.asarray(src_pts, dtype=np.float32),
            np.asarray(dst_pts, dtype=np.float32),
            method=cv2.LMEDS,
        )
        if matrix is None:
            return None
        return cv2.warpAffine(
            image_bgr, matrix, (ALIGNED_SIZE, ALIGNED_SIZE),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )


class ArcFaceEmbedder:
    """w600k_mbf -> 512-d L2-normalized identity vector."""

    def __init__(self, model_path: Path = RECOGNISER_MODEL):
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        if aligned_bgr is None:
            raise ValueError("cannot embed a missing aligned face")
        if aligned_bgr.shape[0] != ALIGNED_SIZE or aligned_bgr.shape[1] != ALIGNED_SIZE:
            aligned_bgr = cv2.resize(aligned_bgr, (ALIGNED_SIZE, ALIGNED_SIZE))
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        x = (rgb - 127.5) / 127.5              # same scale/bias as tools/convert_arcface.py
        x = x.transpose(2, 0, 1)[None]
        raw = self.session.run([self.output_name], {self.input_name: x})[0].reshape(-1)
        if raw.size != EMBEDDING_DIM:
            raise RuntimeError(f"expected {EMBEDDING_DIM} floats, got {raw.size}")
        return l2_normalize(raw)


# --------------------------------------------------------------------------- #
# identity store
# --------------------------------------------------------------------------- #
@dataclass
class Identity:
    name: str
    samples: list[np.ndarray] = field(default_factory=list)

    @property
    def template(self) -> np.ndarray:
        """Centroid of all samples, re-normalized (Glance's `template`)."""
        if not self.samples:
            raise ValueError("identity has no samples")
        return l2_normalize(np.mean(np.stack(self.samples), axis=0))

    def score(self, embedding: np.ndarray) -> tuple[float, float]:
        centroid = cosine(embedding, self.template)
        nearest = max(cosine(embedding, s) for s in self.samples)
        return centroid, nearest


class IdentityStore:
    """JSON on disk. Phase 3 swaps the plaintext embeddings for DPAPI-sealed blobs."""

    def __init__(self, path: Path):
        self.path = path
        self.identities: list[Identity] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.identities = [
            Identity(name=i["name"], samples=[np.array(s, dtype=np.float32) for s in i["samples"]])
            for i in data.get("identities", [])
        ]

    def save(self) -> None:
        payload = {
            "version": 1,
            "model": "arcface-w600k_mbf-v1",
            "identities": [
                {"name": i.name, "samples": [s.tolist() for s in i.samples]} for i in self.identities
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def add_sample(self, name: str, embedding: np.ndarray) -> Identity:
        identity = next((i for i in self.identities if i.name == name), None)
        if identity is None:
            identity = Identity(name=name)
            self.identities.append(identity)
        identity.samples.append(embedding.astype(np.float32))
        return identity

    def best_match(self, embedding: np.ndarray, threshold: float = DEFAULT_THRESHOLD):
        """Both the centroid and the nearest sample must clear the threshold.

        That is FaceRecognitionPipeline.bestMatch: averaging can blur distinct
        appearances together, so the nearest single sample gets a veto.
        """
        best = None
        for identity in self.identities:
            if not identity.samples:
                continue
            centroid, nearest = identity.score(embedding)
            if centroid >= threshold and nearest >= threshold:
                if best is None or centroid > best[1]:
                    best = (identity, centroid, nearest)
        return best


# --------------------------------------------------------------------------- #
# one-shot pipeline
# --------------------------------------------------------------------------- #
class GlancePipeline:
    def __init__(self, store_path: Path | None = None, threshold: float = DEFAULT_THRESHOLD,
                 model_dir: Path | None = None):
        model_dir = Path(model_dir or MODELS_DIR)
        detector_model = model_dir / "det_500m.onnx"
        recogniser_model = model_dir / "w600k_mbf.onnx"
        for model in (detector_model, recogniser_model):
            if not model.is_file():
                raise FileNotFoundError(
                    f"Required face model is missing: {model}. "
                    "Reinstall Glance or run the developer model-fetch command."
                )
        self.detector = FaceDetector(detector_model)
        self.aligner = FaceAligner()
        self.embedder = ArcFaceEmbedder(recogniser_model)
        self.store = IdentityStore(store_path or PATHS.identities_path)
        self.threshold = threshold

    def largest_face(self, frame_bgr: np.ndarray):
        faces = self.detector.detect(frame_bgr)
        return faces[0] if faces else None

    def embedding_for(self, frame_bgr: np.ndarray):
        face = self.largest_face(frame_bgr)
        if face is None:
            return None, None, None
        aligned = self.aligner.align(frame_bgr, face)
        if aligned is None:
            return None, face, None
        return self.embedder.embed(aligned), face, aligned

    @staticmethod
    def open_camera(index: int = 0) -> cv2.VideoCapture:
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                # 960x540 is plenty: the detector downsizes to 640 anyway, and a 720p
                # capture on this class of laptop costs more than it adds.
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
                cap.set(cv2.CAP_PROP_FPS, 15)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            cap.release()
        raise RuntimeError(
            f"Could not open camera {index}. Check Settings > Privacy > Camera, "
            "and that no other app (Teams, Zoom) has it open."
        )


# --------------------------------------------------------------------------- #
# drawing helpers
# --------------------------------------------------------------------------- #
def draw_face(frame, face: DetectedFace) -> None:
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 200, 120), 2)
    for px, py in face.kps:
        cv2.circle(frame, (int(px), int(py)), 3, (0, 220, 255), -1)


def draw_banner(frame, text: str, color=(255, 255, 255), y: int = 40) -> None:
    cv2.putText(frame, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)


def fps_meter() -> "FpsMeter":
    return FpsMeter()


class FpsMeter:
    def __init__(self) -> None:
        self.last = time.perf_counter()
        self.value = 0.0

    def tick(self) -> float:
        now = time.perf_counter()
        dt = now - self.last
        self.last = now
        if dt > 0:
            self.value = 0.9 * self.value + 0.1 * (1.0 / dt)
        return self.value
