"""
Glance for Windows - Phase 2 liveness.

Ported from glance/Liveness/*.swift. The decision model is the part worth
copying exactly, because it is unusually well thought out:

  * five independent cues, never combined into a score
  * DENY cues (glare, device bezel) override CONFIRM cues unconditionally
  * a cue with confidence 0 abstains - it can neither convict nor acquit
  * firing is latched per scan, and counting is cumulative not consecutive,
    so a single-frame landmark dropout doesn't reset progress
  * Light mode  = deny only, "confirmed unless proven wrong"  <- Glance default
  * Heavy mode  = deny cues, plus at least one CONFIRM cue must fire

Implemented and tested here: the two deny cues. They need no landmarks, and on
Glance's own tuning the device-rectangle detector is described as "the one
signal proven reliable in real-device testing".

Partially implemented:
  * blink  - eye aspect ratio from 106-point landmarks, assigned to eye anchors
             geometrically (see notes below on why not by hardcoded index)

Deliberately abstaining for now:
  * flatVs3D, depthPose - these need a head-yaw source. Glance gets yaw free
    from Vision's VNFaceObservation. There is no equivalent here, and deriving
    yaw from the nose offset it is then correlated against would make the cue
    trivially self-confirming. An honest abstention beats a fake pass.

Landmark note: Glance indexes Vision landmark *regions* (leftEye, nose, ...).
The 106-point ONNX model emits an ordered flat array whose region boundaries are
NOT the commonly published 33/10/9/5/8/10/10/11/10 layout - that layout failed a
geometric sanity check on real frames (eyes came out stacked vertically). So
regions are assigned by proximity to the facial anchors SCRFD already produces,
which is self-calibrating per face instead of trusting an unverified index table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

# --------------------------------------------------------------------------- #
# cue vocabulary - mirrors LivenessCues.swift
# --------------------------------------------------------------------------- #
class CueRole(Enum):
    DENY = "deny"
    CONFIRM = "confirm"


class Cue(Enum):
    GLOSS_GLARE = "glossGlare"
    DEVICE_DETECTED = "deviceDetected"
    FLAT_VS_3D = "flatVs3D"
    DEPTH_POSE = "depthPose"
    BLINK = "blink"

    @property
    def role(self) -> CueRole:
        return CueRole.DENY if self in (Cue.GLOSS_GLARE, Cue.DEVICE_DETECTED) else CueRole.CONFIRM

    @property
    def title(self) -> str:
        return {
            Cue.GLOSS_GLARE: "Gloss/glare",
            Cue.DEVICE_DETECTED: "Device detected",
            Cue.FLAT_VS_3D: "Flat vs 3D",
            Cue.DEPTH_POSE: "Depth/pose",
            Cue.BLINK: "Blink",
        }[self]


@dataclass(frozen=True)
class CueReading:
    """confidence == 0 is always an abstention, never a reading of zero."""
    level: float = 0.0
    confidence: float = 0.0

    @staticmethod
    def none() -> "CueReading":
        return CueReading(0.0, 0.0)


class LivenessMode(Enum):
    LIGHT = "light"
    HEAVY = "heavy"


@dataclass
class LivenessTuning:
    """Defaults copied from LivenessTuning in LivenessCues.swift."""
    gloss_level: float = 0.04
    gloss_frames: int = 3
    device_level: float = 0.15
    device_frames: int = 3
    flat_vs_3d_level: float = 0.25
    flat_vs_3d_frames: int = 2
    depth_pose_level: float = 0.8
    depth_pose_frames: int = 2
    blink_frames: int = 1
    light_mode_minimum_frames: int = 3

    def level(self, cue: Cue) -> float:
        return {
            Cue.GLOSS_GLARE: self.gloss_level,
            Cue.DEVICE_DETECTED: self.device_level,
            Cue.FLAT_VS_3D: self.flat_vs_3d_level,
            Cue.DEPTH_POSE: self.depth_pose_level,
            Cue.BLINK: 0.5,
        }[cue]

    def frames(self, cue: Cue) -> int:
        return {
            Cue.GLOSS_GLARE: self.gloss_frames,
            Cue.DEVICE_DETECTED: self.device_frames,
            Cue.FLAT_VS_3D: self.flat_vs_3d_frames,
            Cue.DEPTH_POSE: self.depth_pose_frames,
            Cue.BLINK: self.blink_frames,
        }[cue]


class Decision(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"


@dataclass
class CueState:
    reading: CueReading = field(default_factory=CueReading.none)
    frames_counted: int = 0
    has_fired: bool = False

    def progress(self, threshold: int) -> float:
        if threshold <= 0:
            return 1.0 if self.has_fired else 0.0
        return min(1.0, self.frames_counted / threshold)


@dataclass
class Snapshot:
    decision: Decision
    denied_by: Cue | None
    confirmed_by: Cue | None
    states: dict[Cue, CueState]
    frame_count: int

    def denial_reason(self) -> str | None:
        if self.decision is not Decision.DENIED:
            return None
        if self.denied_by is Cue.GLOSS_GLARE:
            return "Screen glare detected - this looks like a photo on a display."
        if self.denied_by is Cue.DEVICE_DETECTED:
            return "A device-shaped rectangle was detected around the face - photo or screen."
        return "Liveness check failed."


class LivenessEvaluator:
    """Port of LivenessEvaluator. Deny is evaluated first and is unconditional."""

    def __init__(self, mode: LivenessMode = LivenessMode.LIGHT,
                 tuning: LivenessTuning | None = None,
                 enabled_cues: set[Cue] | None = None):
        self.mode = mode
        self.tuning = tuning or LivenessTuning()
        self.enabled_cues = enabled_cues if enabled_cues is not None else set(Cue)
        self.states: dict[Cue, CueState] = {}
        self.frames_observed = 0

    def reset(self) -> None:
        self.states = {}
        self.frames_observed = 0

    def observe(self, readings: dict[Cue, CueReading]) -> Snapshot:
        self.frames_observed += 1
        for cue in Cue:
            state = self.states.setdefault(cue, CueState())
            reading = readings.get(cue, CueReading.none())
            state.reading = reading
            if reading.confidence > 0 and reading.level >= self.tuning.level(cue):
                state.frames_counted += 1
                if state.frames_counted >= self.tuning.frames(cue):
                    state.has_fired = True
        return self._snapshot()

    def _snapshot(self) -> Snapshot:
        decision, denied_by, confirmed_by = Decision.PENDING, None, None

        for cue in Cue:
            if cue.role is CueRole.DENY and cue in self.enabled_cues and self.states[cue].has_fired:
                decision, denied_by = Decision.DENIED, cue
                break

        if decision is not Decision.DENIED:
            if self.mode is LivenessMode.LIGHT:
                if self.frames_observed >= self.tuning.light_mode_minimum_frames:
                    decision = Decision.CONFIRMED
            else:
                for cue in Cue:
                    if (cue.role is CueRole.CONFIRM and cue in self.enabled_cues
                            and self.states[cue].has_fired):
                        decision, confirmed_by = Decision.CONFIRMED, cue
                        break

        return Snapshot(decision, denied_by, confirmed_by, self.states, self.frames_observed)


# --------------------------------------------------------------------------- #
# deny cue 1: gloss / glare   (GlareCueExtractor.swift)
# --------------------------------------------------------------------------- #
@dataclass
class GlareSample:
    crop_pixel_width: float
    specular_fraction: float
    specular_cluster_ratio: float


SPECULAR_LUMA_FLOOR = 235.0
SPECULAR_CHROMA_TOLERANCE = 10.0
CLUSTER_GRID_SIZE = 8


def extract_glare(face_crop_bgr: np.ndarray) -> GlareSample | None:
    """Skin gives many small scattered specular points; glass gives one big flat blob.

    Vectorised version of the Swift per-pixel loop - same arithmetic, same
    thresholds, but numpy so it doesn't burn a slow CPU pixel by pixel.
    """
    if face_crop_bgr is None or face_crop_bgr.size == 0:
        return None
    h, w = face_crop_bgr.shape[:2]
    if h == 0 or w == 0:
        return None

    px = face_crop_bgr.astype(np.float32)
    b, g, r = px[:, :, 0], px[:, :, 1], px[:, :, 2]

    luma = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0

    mask = ((luma >= SPECULAR_LUMA_FLOOR)
            & (np.abs(cb - 128.0) <= SPECULAR_CHROMA_TOLERANCE)
            & (np.abs(cr - 128.0) <= SPECULAR_CHROMA_TOLERANCE))

    specular_total = int(mask.sum())
    pixel_count = h * w
    specular_fraction = specular_total / pixel_count

    cluster_ratio = 0.0
    if specular_total > 0:
        ys, xs = np.nonzero(mask)
        gy = np.minimum(CLUSTER_GRID_SIZE - 1, ys * CLUSTER_GRID_SIZE // h)
        gx = np.minimum(CLUSTER_GRID_SIZE - 1, xs * CLUSTER_GRID_SIZE // w)
        counts = np.bincount(gy * CLUSTER_GRID_SIZE + gx,
                             minlength=CLUSTER_GRID_SIZE ** 2)
        cluster_ratio = float(counts.max()) / float(specular_total)

    return GlareSample(float(w), specular_fraction, cluster_ratio)


def _ramp(value: float, floor: float, ceiling: float) -> float:
    return float(min(max((value - floor) / max(ceiling - floor, 1e-4), 0.0), 1.0))


def gloss_glare_reading(sample: GlareSample | None) -> CueReading:
    if sample is None:
        return CueReading.none()
    fraction_score = _ramp(sample.specular_fraction, 0.01, 0.08)
    cluster_factor = _ramp(sample.specular_cluster_ratio, 0.3, 1.0)
    level = fraction_score * (0.3 + 0.7 * cluster_factor)
    # Below ~50 native px of face there isn't enough detail to tell a glare blob
    # from a bright patch; full trust by ~130px.
    confidence = _ramp(sample.crop_pixel_width, 50, 130)
    return CueReading(level, confidence)


# --------------------------------------------------------------------------- #
# deny cue 2: device bezel   (DeviceBezelDetector.swift)
# --------------------------------------------------------------------------- #
@dataclass
class DeviceObservation:
    rectangle: tuple[int, int, int, int] | None = None
    face_overlap_fraction: float | None = None

    @staticmethod
    def none() -> "DeviceObservation":
        return DeviceObservation(None, None)


MIN_CONFIDENCE = 0.6          # unused by OpenCV path, kept for parity/documentation
MIN_SIZE_FRACTION = 0.15      # fraction of image AREA, not width/height
MIN_ASPECT = 0.35             # phone in portrait ... near-square tablet crop
MAX_ASPECT = 1.0
QUADRATURE_TOLERANCE_DEG = 30.0


def detect_device_rectangle(frame_bgr: np.ndarray,
                            face_bbox: tuple[float, float, float, float]) -> DeviceObservation:
    """Find a device-shaped rectangle (phone/tablet) overlapping the face.

    Vision's VNDetectRectanglesRequest works on edges/gradients, not brightness,
    so this matches that: Canny -> dilate (so a thin bezel outline becomes one
    closed band) -> external contours -> polygonal approximation -> the same
    geometric rules Glance uses: area >= 15% of the frame, aspect 0.35..1.0,
    corners within 30 degrees of square.

    Brightness thresholding was tried first and rejected: in a normally lit room
    a bright wall merges with a bright screen and the contour becomes the whole
    frame (measured 62% of the frame as one blob on real footage).
    """
    h, w = frame_bgr.shape[:2]
    frame_area = float(h * w)
    min_area = MIN_SIZE_FRACTION * frame_area

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # Close the outline into a solid band so its external contour encloses area.
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    fx1, fy1, fx2, fy2 = face_bbox
    face_area = max((fx2 - fx1) * (fy2 - fy1), 1.0)

    best = None
    best_score = -1.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        peri = cv2.arcLength(contour, True)
        approx = None
        for epsilon in (0.02, 0.03, 0.04, 0.05, 0.06):
            candidate = cv2.approxPolyDP(contour, epsilon * peri, True)
            if len(candidate) == 4 and cv2.isContourConvex(candidate):
                approx = candidate
                break
        if approx is None:
            continue

        (_, _), (rw, rh), _ = cv2.minAreaRect(contour)
        if rw <= 0 or rh <= 0:
            continue
        aspect = min(rw, rh) / max(rw, rh)
        if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
            continue
        pts = approx.reshape(4, 2).astype(np.float64)
        if not _corners_near_square(pts, QUADRATURE_TOLERANCE_DEG):
            continue

        bx, by, bw, bh = cv2.boundingRect(approx)
        ix1, iy1 = max(bx, fx1), max(by, fy1)
        ix2, iy2 = min(bx + bw, fx2), min(by + bh, fy2)
        overlap = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1) / face_area
        if overlap <= 0.0:
            continue
        # Prefer the candidate covering the most of the face, then the largest.
        score = overlap * 10.0 + area / frame_area
        if score > best_score:
            best_score = score
            best = (bx, by, bw, bh)
            best_overlap = overlap

    if best is None:
        return DeviceObservation.none()
    return DeviceObservation(best, float(min(max(best_overlap, 0.0), 1.0)))


def _corners_near_square(pts: np.ndarray, tolerance_deg: float) -> bool:
    n = len(pts)
    for i in range(n):
        p0, p1, p2 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        v1, v2 = p0 - p1, p2 - p1
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            return False
        cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        if abs(np.degrees(np.arccos(cos_a)) - 90.0) > tolerance_deg:
            return False
    return True


def device_reading(obs: DeviceObservation) -> CueReading:
    if obs.face_overlap_fraction is None:
        return CueReading.none()
    return CueReading(float(min(max(obs.face_overlap_fraction, 0.0), 1.0)), 1.0)


# --------------------------------------------------------------------------- #
# confirm cue: blink   (LivenessScoring.blinkDynamics)
# --------------------------------------------------------------------------- #
def eye_points_by_proximity(landmarks_106: np.ndarray,
                            left_eye: np.ndarray,
                            right_eye: np.ndarray,
                            k: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Split 106 landmarks into two eye clusters by proximity to SCRFD's eye anchors.

    Index tables for the 106-point model are widely misquoted, so this assigns
    by geometry instead: the k nearest points to each verified eye centre.
    """
    pts = np.asarray(landmarks_106, dtype=np.float32)
    d_left = np.linalg.norm(pts - np.asarray(left_eye, np.float32), axis=1)
    d_right = np.linalg.norm(pts - np.asarray(right_eye, np.float32), axis=1)
    # one pass of mutual exclusion so the same point can't join both eyes
    order_left = np.argsort(d_left)
    chosen_left = []
    for i in order_left:
        if len(chosen_left) >= k:
            break
        if d_right[i] < d_left[i]:
            continue
        chosen_left.append(i)
    order_right = np.argsort(d_right)
    chosen_right = []
    for i in order_right:
        if len(chosen_right) >= k:
            break
        if i in chosen_left or d_left[i] < d_right[i]:
            continue
        chosen_right.append(i)
    return pts[chosen_left], pts[chosen_right]


def eye_aspect_ratio(eye_pts: np.ndarray) -> float | None:
    if eye_pts is None or len(eye_pts) < 3:
        return None
    w = float(np.ptp(eye_pts[:, 0]))
    h = float(np.ptp(eye_pts[:, 1]))
    if w <= 1e-6:
        return None
    return h / w


def blink_reading(ear_history: list[float]) -> CueReading:
    """Dip-and-recovery in eye aspect ratio. A photo cannot blink.

    Port of LivenessScoring.blinkDynamics: needs a dip below 0.65 of baseline
    with recovery to >0.7 on at least one side within 3 frames. Absence of a
    blink is an abstention, never a failure - a live person may not blink in a
    short scan.
    """
    if len(ear_history) < 4:
        return CueReading.none()
    ears = ear_history[-8:]
    baseline = max(ears)
    if baseline <= 0:
        return CueReading.none()
    normalised = [e / baseline for e in ears]
    min_index = int(np.argmin(normalised))
    dip = normalised[min_index]
    if dip >= 0.65:
        return CueReading.none()

    radius = 3
    before = normalised[max(0, min_index - radius):min_index]
    after = normalised[min_index + 1:min_index + 1 + radius]
    open_before = any(v > 0.7 for v in before)
    open_after = any(v > 0.7 for v in after)
    if min_index > 0 and min_index < len(normalised) - 1 and open_before and open_after:
        return CueReading(1.0, 1.0)
    return CueReading.none()


# --------------------------------------------------------------------------- #
# per-frame readings
# --------------------------------------------------------------------------- #
def readings(glare: GlareSample | None,
             device: DeviceObservation | None,
             ear_history: list[float] | None = None) -> dict[Cue, CueReading]:
    out = {
        Cue.GLOSS_GLARE: gloss_glare_reading(glare),
        Cue.DEVICE_DETECTED: device_reading(device) if device else CueReading.none(),
        # Abstain: no head-yaw source on Windows. See the module docstring.
        Cue.FLAT_VS_3D: CueReading.none(),
        Cue.DEPTH_POSE: CueReading.none(),
        Cue.BLINK: blink_reading(ear_history or []),
    }
    return out
