"""Camera + face pipeline running off the UI thread.

The ONNX sessions and the capture loop live here, so the window never blocks while a
frame is being embedded. Results cross the thread boundary as plain data.

Rates are deliberately low. On a 2-core laptop the camera thread competes with the
compositor: detection at 5 Hz and an embedding once a second is enough to draw a box and
a name, while running both on every frame made the whole window feel stuck.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from ..core import GlancePipeline

GOOD_FACE_FRACTION = 0.02   # 2% of the frame - a laptop webcam at arm's length
GOOD_BLUR = 60.0            # variance of the Laplacian
MIN_BLUR = 20.0

PREVIEW_MAX_WIDTH = 640
PREVIEW_INTERVAL = 1 / 12.0
DETECT_INTERVAL = 0.20      # 5 detections per second while the preview is open
EMBED_INTERVAL = 1.0        # refresh the name label once a second
GATE_INTERVAL = 0.5         # in-app face check status twice a second


def full_face_in_frame(face, frame_shape) -> bool:
    """Require a small safety margin around the detector's full face box."""
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = face.bbox
    return (x1 >= width * .025 and y1 >= height * .025 and
            x2 <= width * .975 and y2 <= height * .975)


@dataclass
class Capture:
    embedding: np.ndarray | None = None
    face_fraction: float = 0.0
    blur: float = 0.0
    match_name: str = ""
    match_score: float = 0.0
    ok: bool = False
    message: str = ""
    pose: tuple[float, float] | None = None


@dataclass
class FrameInfo:
    faces: list = field(default_factory=list)   # [(x1, y1, x2, y2), ...]
    label: str = ""
    score: float = 0.0
    hint: str = ""


class CameraWorker(QThread):
    preview = Signal(QImage, object)    # image, FrameInfo
    captured = Signal(object)           # Capture
    gate = Signal(str, float, str)      # name, score, hint (in-app face check)
    ready = Signal()                    # opened camera and read first frame
    failed = Signal(str)

    def __init__(self, camera_index: int = 0, threshold: float = 0.60, parent=None,
                 *, pipeline_factory=GlancePipeline):
        super().__init__(parent)
        self._index = camera_index
        self._threshold = threshold
        self._pipeline_factory = pipeline_factory
        self._stop = threading.Event()
        self._capture = threading.Event()
        self._capture_pending = threading.Event()
        self._ready = threading.Event()
        self._reload = threading.Event()
        self._gate = threading.Event()
        self._lock = threading.Lock()

    def start(self, priority=QThread.Priority.InheritPriority) -> None:
        """Restart only after the prior run ended, with fresh request state."""
        if self.isRunning():
            return
        self._stop.clear()
        self._capture.clear()
        self._capture_pending.clear()
        self._ready.clear()
        self._reload.clear()
        self._gate.clear()
        super().start(priority)

    # -- control ----------------------------------------------------------
    def set_threshold(self, value: float) -> None:
        with self._lock:
            self._threshold = float(value)

    def set_camera(self, index: int) -> None:
        with self._lock:
            self._index = int(index)

    def request_capture(self) -> bool:
        with self._lock:
            if (not self.isRunning() or not self._ready.is_set()
                    or self._capture_pending.is_set()):
                return False
            self._capture_pending.set()
            self._capture.set()
            return True

    def reload_store(self) -> None:
        self._reload.set()

    def set_gate(self, active: bool) -> None:
        """Quietly report matches for the in-app face check without capturing."""
        if active:
            self._gate.set()
        else:
            self._gate.clear()

    def gate_active(self) -> bool:
        return self._gate.is_set()

    def stop(self) -> None:
        self._stop.set()
        self._capture.clear()
        self._capture_pending.clear()
        self._ready.clear()
        self._gate.clear()

    # -- thread -----------------------------------------------------------
    def run(self) -> None:
        try:
            pipeline = self._pipeline_factory(threshold=self._threshold)
        except Exception as error:  # noqa: BLE001 - surface any model problem to the UI
            self._ready.clear()
            self._capture_pending.clear()
            self.failed.emit(f"could not load the face models: {error}")
            return

        camera = None
        opened_index: int | None = None
        faces: list = []
        label, score = "", 0.0
        last_detect = last_embed = last_preview = last_gate = 0.0

        try:
            while not self._stop.is_set():
                with self._lock:
                    index = self._index
                    threshold = self._threshold
                pipeline.threshold = threshold

                if camera is None:
                    try:
                        camera = pipeline.open_camera(index)
                        opened_index = index
                    except Exception as error:  # noqa: BLE001
                        self._ready.clear()
                        self._capture_pending.clear()
                        self.failed.emit(str(error))
                        return

                elif opened_index != index:
                    camera.release()
                    camera = None
                    opened_index = None
                    self._ready.clear()
                    continue

                ok, frame = camera.read()
                if not ok or frame is None:
                    self._ready.clear()
                    self._capture_pending.clear()
                    self.failed.emit("the camera stopped returning frames")
                    return

                if not self._ready.is_set():
                    self._ready.set()
                    self.ready.emit()

                if self._reload.is_set():
                    self._reload.clear()
                    try:
                        pipeline.store.load()
                    except Exception:  # noqa: BLE001 - a half-written file is not fatal
                        pass

                now = time.perf_counter()
                pending = self._capture.is_set()

                if pending or now - last_detect >= DETECT_INTERVAL:
                    faces = pipeline.detector.detect(frame)
                    last_detect = now

                if pending:
                    self._capture.clear()
                    try:
                        capture = self._make_capture(pipeline, frame, faces, threshold)
                    except Exception as error:  # noqa: BLE001 - report a retryable capture error
                        capture = Capture(message=f"Capture failed: {error}")
                    finally:
                        self._capture_pending.clear()
                    self.captured.emit(capture)
                    if capture.match_name:
                        label, score = capture.match_name, capture.match_score
                elif not self._gate.is_set() and faces and now - last_embed >= EMBED_INTERVAL:
                    aligned = pipeline.aligner.align(frame, faces[0])
                    if aligned is not None:
                        embedding = pipeline.embedder.embed(aligned)
                        match = pipeline.store.best_match(embedding, threshold)
                        label, score = (match[0].name, match[2]) if match else ("", 0.0)
                        last_embed = now

                if self._gate.is_set() and now - last_gate >= GATE_INTERVAL:
                    last_gate = now
                    if not faces:
                        self.gate.emit("", 0.0, "No face in view")
                    elif len(faces) > 1:
                        self.gate.emit("", 0.0, "Only one face at a time")
                    else:
                        # Embed fresh for every gate result so three stable
                        # matches really are three independent readings.
                        aligned = pipeline.aligner.align(frame, faces[0])
                        name, score = "", 0.0
                        if aligned is not None:
                            embedding = pipeline.embedder.embed(aligned)
                            match = pipeline.store.best_match(embedding, threshold)
                            if match:
                                name, score = match[0].name, match[2]
                        height, width = frame.shape[:2]
                        small = faces[0].area / float(width * height) < GOOD_FACE_FRACTION
                        self.gate.emit(name, score, "Move closer" if small else "")

                if now - last_preview >= PREVIEW_INTERVAL:
                    last_preview = now
                    image, info = self._preview(frame, faces, label, score)
                    self.preview.emit(image, info)

                time.sleep(0.004)
        finally:
            if camera is not None:
                camera.release()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _preview(frame: np.ndarray, faces: list, label: str, score: float):
        height, width = frame.shape[:2]
        hint = ""
        if not faces:
            hint = "No face"
        elif len(faces) > 1:
            hint = "More than one face"
        elif faces[0].area / float(width * height) < GOOD_FACE_FRACTION:
            hint = "Move closer"

        scale = 1.0
        if width > PREVIEW_MAX_WIDTH:
            scale = PREVIEW_MAX_WIDTH / float(width)
            small = cv2.resize(
                frame, (PREVIEW_MAX_WIDTH, int(round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            small = frame

        boxes = [
            (x1 * scale, y1 * scale, x2 * scale, y2 * scale)
            for (x1, y1, x2, y2) in (tuple(float(v) for v in face.bbox) for face in faces)
        ]
        return CameraWorker._to_image(small), FrameInfo(boxes, label, score, hint)

    def _make_capture(self, pipeline, frame, faces, threshold) -> Capture:
        if not faces:
            return Capture(message="No face in view")
        if len(faces) > 1:
            return Capture(message="Only one face at a time")

        height, width = frame.shape[:2]
        fraction = faces[0].area / float(width * height)
        x1, y1, x2, y2 = faces[0].bbox
        if not full_face_in_frame(faces[0], frame.shape):
            return Capture(message="Move back: keep your full face inside the frame")
        if fraction < GOOD_FACE_FRACTION:
            return Capture(message="Move closer to the camera")
        points = np.asarray(faces[0].kps)
        if (points.shape != (5, 2) or not np.isfinite(points).all() or
                (points[:, 0] <= x1).any() or (points[:, 0] >= x2).any() or
                (points[:, 1] <= y1).any() or (points[:, 1] >= y2).any()):
            return Capture(message="Keep both eyes, nose and mouth visible")
        eyes = (points[0] + points[1]) / 2
        mouth = (points[3] + points[4]) / 2
        eye_width = float(np.linalg.norm(points[1] - points[0]))
        vertical = float(mouth[1] - eyes[1])
        if eye_width < 10 or vertical < 10:
            return Capture(message="Face the camera and move closer")
        pose = (float((points[2, 0] - eyes[0]) / eye_width),
                float((points[2, 1] - eyes[1]) / vertical))
        aligned = pipeline.aligner.align(frame, faces[0])
        if aligned is None:
            return Capture(message="Could not align the face")

        gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if blur < GOOD_BLUR:
            return Capture(message="The image is blurry; hold still in even light")
        brightness = float(gray.mean())
        if not 35 <= brightness <= 225:
            return Capture(message="Use even lighting; avoid darkness or bright backlight")
        embedding = pipeline.embedder.embed(aligned)

        score_name, score = "", 0.0
        match = pipeline.store.best_match(embedding, threshold)
        if match:
            score_name, score = match[0].name, match[2]

        if fraction < GOOD_FACE_FRACTION:
            return Capture(embedding, fraction, blur, score_name, score,
                           message="Move closer to the camera")
        if blur < MIN_BLUR:
            return Capture(embedding, fraction, blur, score_name, score,
                           message="The image is blurry - hold still")
        if blur < GOOD_BLUR:
            return Capture(embedding, fraction, blur, score_name, score, ok=True,
                           message="Slightly blurry, but usable")

        return Capture(embedding, fraction, blur, score_name, score, ok=True,
                       message="Good sample", pose=pose)

    @staticmethod
    def _to_image(frame: np.ndarray) -> QImage:
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, 3 * width, QImage.Format_BGR888)
        return image.copy()
