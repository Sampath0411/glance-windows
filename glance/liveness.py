"""
Glance for Windows - Phase 2 liveness.

Ported from glance/Liveness/*.swift. The decision model is the part worth
copying exactly, because it is unusually well thought out:

  * independent cues, never combined into a score
  * DENY cues override CONFIRM cues unconditionally
  * a cue with confidence 0 abstains - it can neither convict nor acquit
  * firing is latched per scan, and counting is cumulative not consecutive,
    so a single-frame landmark dropout doesn't reset progress
  * Light mode  = deny only, "confirmed unless proven wrong"  <- diagnostics only
  * Heavy/Strict = deny cues, plus a forward→left→right→forward head-turn challenge

Implemented deny cues:
  * glossGlare       - one flat specular blob instead of scattered skin glints
  * screenGlow       - a bright, neutral, uniform emissive panel behind the face
  * deviceDetected   - device-shaped rectangle covering most of or tightly
                       enclosing the face; partial background overlaps abstain
  * printOrMoire     - periodic high-frequency peaks: display pixel grid / moire
                       or a printed halftone screen

Implemented confirm cues:
  * headTurn          - ordered relative pose change: forward, left, right, forward
  * blink/microMotion - retained for diagnostics only; never authorization cues

Head pose limitation:
  * The SCRFD landmark yaw proxy can guide this prompted challenge but cannot
    provide calibrated 3D pose or prove physical presence.

Known limit: these RGB-camera heuristics are not proof of physical presence.
Video replay, illumination changes, and alignment errors can fool pixel cues.
Real-camera spoof validation is necessary; synthetic checks do not establish
that all phones or printed photos are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

HEAD_TURN_BASELINE_SAMPLES = 4
HEAD_TURN_SAMPLES = 3
HEAD_TURN_RETURN_SAMPLES = 3
HEAD_TURN_MIN_DELTA = 0.12
HEAD_TURN_CENTER_TOLERANCE = 0.055
HEAD_TURN_MAX_BASELINE_OFFSET = 0.12
MIN_HEAD_POSE_CONFIDENCE = 0.35

# --------------------------------------------------------------------------- #
# cue vocabulary - mirrors LivenessCues.swift
# --------------------------------------------------------------------------- #
class CueRole(Enum):
    DENY = "deny"
    CONFIRM = "confirm"


class Cue(Enum):
    GLOSS_GLARE = "glossGlare"
    SCREEN_GLOW = "screenGlow"
    DEVICE_DETECTED = "deviceDetected"
    PRINT_OR_MOIRE = "printOrMoire"
    FLAT_VS_3D = "flatVs3D"
    DEPTH_POSE = "depthPose"
    BLINK = "blink"
    HEAD_TURN = "headTurn"
    MICRO_MOTION = "microMotion"

    @property
    def role(self) -> CueRole:
        return CueRole.DENY if self in (
            Cue.GLOSS_GLARE, Cue.SCREEN_GLOW, Cue.DEVICE_DETECTED, Cue.PRINT_OR_MOIRE,
        ) else CueRole.CONFIRM

    @property
    def title(self) -> str:
        return {
            Cue.GLOSS_GLARE: "Gloss/glare",
            Cue.SCREEN_GLOW: "Screen glow",
            Cue.DEVICE_DETECTED: "Device detected",
            Cue.PRINT_OR_MOIRE: "Print/moire texture",
            Cue.FLAT_VS_3D: "Flat vs 3D",
            Cue.DEPTH_POSE: "Depth/pose",
            Cue.BLINK: "Blink",
            Cue.HEAD_TURN: "Head turn",
            Cue.MICRO_MOTION: "Micro-motion",
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
    STRICT = "strict"

    @classmethod
    def from_name(cls, value) -> "LivenessMode":
        """Unknown or malformed settings fall back to the safe default, not to off."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.HEAVY


# Default for the unlock agent. Light is deliberately *not* the default: it
# confirms after three clean frames, which is exactly how a still photo used to
# unlock Windows.
DEFAULT_MODE = LivenessMode.HEAVY


@dataclass
class LivenessTuning:
    """Defaults copied from LivenessTuning in LivenessCues.swift, plus the
    Windows-specific screen/print/motion cues."""
    gloss_level: float = 0.04
    gloss_frames: int = 3
    screen_level: float = 0.62
    screen_frames: int = 3
    device_level: float = 0.15
    device_frames: int = 3
    moire_level: float = 0.60
    moire_frames: int = 4
    flat_vs_3d_level: float = 0.25
    flat_vs_3d_frames: int = 2
    depth_pose_level: float = 0.8
    depth_pose_frames: int = 2
    blink_frames: int = 1
    head_turn_frames: int = 1
    motion_level: float = 0.60
    motion_frames: int = 3
    light_mode_minimum_frames: int = 3

    def level(self, cue: Cue) -> float:
        return {
            Cue.GLOSS_GLARE: self.gloss_level,
            Cue.SCREEN_GLOW: self.screen_level,
            Cue.DEVICE_DETECTED: self.device_level,
            Cue.PRINT_OR_MOIRE: self.moire_level,
            Cue.FLAT_VS_3D: self.flat_vs_3d_level,
            Cue.DEPTH_POSE: self.depth_pose_level,
            Cue.BLINK: 0.5,
            Cue.HEAD_TURN: 0.5,
            Cue.MICRO_MOTION: self.motion_level,
        }[cue]

    def frames(self, cue: Cue) -> int:
        return {
            Cue.GLOSS_GLARE: self.gloss_frames,
            Cue.SCREEN_GLOW: self.screen_frames,
            Cue.DEVICE_DETECTED: self.device_frames,
            Cue.PRINT_OR_MOIRE: self.moire_frames,
            Cue.FLAT_VS_3D: self.flat_vs_3d_frames,
            Cue.DEPTH_POSE: self.depth_pose_frames,
            Cue.BLINK: self.blink_frames,
            Cue.HEAD_TURN: self.head_turn_frames,
            Cue.MICRO_MOTION: self.motion_frames,
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
            return "Possible glare in the face area. Adjust the camera angle or lighting, then retry."
        if self.denied_by is Cue.SCREEN_GLOW:
            return "Screen-glow cue: the area around your face looks unusually bright. Adjust lighting or reposition, then retry."
        if self.denied_by is Cue.DEVICE_DETECTED:
            return "A rectangular edge near the face confused the camera check. Reposition and retry."
        if self.denied_by is Cue.PRINT_OR_MOIRE:
            return "The face image has an unusual repeating texture. Change distance or lighting, then retry."
        return "Liveness check failed."

    def waiting_reason(self) -> str:
        """Describe the required proof of life for user-facing logs."""
        if self.decision is Decision.CONFIRMED:
            return "confirmed"
        return "waiting for forward → left → right → forward head turn"


class LivenessEvaluator:
    """Port of LivenessEvaluator. Deny is evaluated first and is unconditional."""

    def __init__(self, mode: LivenessMode = DEFAULT_MODE,
                 tuning: LivenessTuning | None = None,
                 enabled_cues: set[Cue] | None = None):
        self.mode = LivenessMode.from_name(mode)
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
            elif Cue.HEAD_TURN in self.enabled_cues and self.states[Cue.HEAD_TURN].has_fired:
                decision, confirmed_by = Decision.CONFIRMED, Cue.HEAD_TURN

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
# A device or print held up for the camera has the face occupying a large part
# of it. A doorway or window with a person standing in it is tens of times the
# face area, and must not read as a phone.
MAX_DEVICE_FACE_RATIO = 12.0


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
    face_width = max(fx2 - fx1, 1.0)
    face_height = max(fy2 - fy1, 1.0)

    # A close-up phone can have its bezel outside the frame; what is still
    # visible is the screen edge partly enclosing the face. Search smaller
    # contours too, but only accept them under the stricter enclosing rules.
    candidate_area = max(0.02 * frame_area, 0.5 * face_area, 400.0)

    best = None
    best_score = -1.0
    best_level = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < candidate_area:
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

        enclosing = (
            _encloses_face((bx, by, bw, bh), (fx1, fy1, fx2, fy2))
            and bw > 0 and bh > 0
            # Tight around the face: a doorway or window with a person standing
            # in it is a much larger rectangle with a much smaller face in it.
            and face_width / float(bw) >= 0.25
            and face_height / float(bh) >= 0.18
            and 0.5 <= area / face_area <= 4.0
        )
        # A rectangle merely crossing the face often comes from a window,
        # picture frame, shelf, or cabinet behind a real user. Require the
        # candidate to cover nearly all of the face; otherwise only accept a
        # tight enclosing rectangle with explicit face-relative geometry.
        if (area >= min_area and overlap >= 0.80
                and area / face_area <= MAX_DEVICE_FACE_RATIO):
            level = float(min(max(overlap, 0.0), 1.0))
            score = overlap * 10.0 + area / frame_area
        elif enclosing:
            level = 1.0
            score = 20.0 + area / frame_area
        else:
            continue

        # Prefer the candidate covering the most of the face, then the largest.
        if score > best_score:
            best_score = score
            best = (bx, by, bw, bh)
            best_level = level
            best_overlap = overlap

    if best is None:
        return DeviceObservation.none()
    return DeviceObservation(best, best_level)


def _encloses_face(rect: tuple[int, int, int, int],
                   face_bbox: tuple[float, float, float, float],
                   margin_fraction: float = 0.02) -> bool:
    bx, by, bw, bh = rect
    fx1, fy1, fx2, fy2 = face_bbox
    margin = margin_fraction * max(fx2 - fx1, fy2 - fy1, 1.0)
    return (bx <= fx1 - margin and by <= fy1 - margin
            and bx + bw >= fx2 + margin and by + bh >= fy2 + margin)



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
# deny cue 3: emissive screen glow
# --------------------------------------------------------------------------- #
@dataclass
class ScreenSample:
    """Statistics of the band of frame immediately around the face box."""
    ring_luma: float
    ring_saturation: float
    ring_uniformity: float      # 1 - std/mean; 1.0 is perfectly flat
    ring_edge_density: float    # share of ring pixels on a Canny edge
    pixel_width: float          # face width in native pixels


def extract_screen_glow(frame_bgr: np.ndarray,
                        face_bbox: tuple[float, float, float, float]) -> ScreenSample | None:
    """Look at the ring of background *around* the face for an emissive panel.

    A held-up phone or a monitor showing a photo is a flat light source right
    behind the face: bright, colour-neutral, almost no texture, and without the
    soft falloff of a lit wall. This deliberately only fires on the extreme
    combination; the confirm cues remain the load-bearing defence.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = [int(round(float(v))) for v in face_bbox]
    face_width, face_height = x2 - x1, y2 - y1
    if face_width <= 8 or face_height <= 8:
        return None

    span = max(face_width, face_height)
    pad_out = int(round(0.35 * span))
    pad_in = int(round(0.05 * span))
    ex1, ey1 = max(0, x1 - pad_out), max(0, y1 - pad_out)
    ex2, ey2 = min(w, x2 + pad_out), min(h, y2 + pad_out)
    band = frame_bgr[ey1:ey2, ex1:ex2]
    if band.size == 0:
        return None

    ring = np.ones(band.shape[:2], dtype=bool)
    ix1, iy1 = max(0, x1 - pad_in - ex1), max(0, y1 - pad_in - ey1)
    ix2 = min(band.shape[1], x2 + pad_in - ex1)
    iy2 = min(band.shape[0], y2 + pad_in - ey1)
    ring[iy1:iy2, ix1:ix2] = False
    if int(ring.sum()) < 600:
        return None

    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    luma = gray[ring].astype(np.float32)
    mean = float(luma.mean())
    std = float(luma.std())
    saturation = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 1][ring].astype(np.float32)
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float((edges[ring] > 0).mean())
    return ScreenSample(
        ring_luma=mean,
        ring_saturation=float(saturation.mean()),
        ring_uniformity=1.0 - std / max(mean, 1.0),
        ring_edge_density=edge_density,
        pixel_width=float(face_width),
    )


def screen_glow_reading(sample: ScreenSample | None) -> CueReading:
    if sample is None:
        return CueReading.none()
    brightness = _ramp(sample.ring_luma, 195.0, 235.0)
    neutrality = _ramp(45.0 - sample.ring_saturation, 0.0, 35.0)
    flatness = _ramp(sample.ring_uniformity, 0.82, 0.95)
    smoothness = _ramp(0.055 - sample.ring_edge_density, 0.0, 0.045)
    level = min(brightness, neutrality) * (0.5 + 0.5 * flatness) * (0.5 + 0.5 * smoothness)
    confidence = _ramp(sample.pixel_width, 70.0, 150.0)
    return CueReading(level, confidence)


# --------------------------------------------------------------------------- #
# deny cue 4: display grid / printed halftone  (periodic texture)
# --------------------------------------------------------------------------- #
@dataclass
class TextureSample:
    peak_ratio: float          # strongest off-axis spectral peak vs mean
    periodic_energy: float     # share of band energy in the top few bins
    contrast: float            # std of the high-passed crop, in luma levels
    crop_size: float           # shorter side of the analysed crop, native px


def extract_texture(face_crop_bgr: np.ndarray) -> TextureSample | None:
    """Find a regular grid in the face crop.

    A screen photographed by a camera, and a printed photo photographed up
    close, both add a periodic pattern that skin does not have. The spectrum is
    analysed off the horizontal/vertical axes so the strong straight edges of
    eyes, glasses and jaw do not masquerade as a grid.
    """
    if face_crop_bgr is None or face_crop_bgr.size == 0:
        return None
    gray = cv2.cvtColor(face_crop_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    if min(h, w) < 48:
        return None
    native = float(min(h, w))

    # Cap the FFT cost on large frames. INTER_AREA keeps the average texture
    # energy, which is what we are measuring.
    target = 256
    if max(h, w) > target:
        scale = target / float(max(h, w))
        gray = cv2.resize(gray, (max(32, int(w * scale)), max(32, int(h * scale))),
                          interpolation=cv2.INTER_AREA)
        h, w = gray.shape[:2]

    data = gray.astype(np.float32)
    # Remove smooth shading and lighting gradients; a periodic grid is fine
    # detail. Without this, any soft background ramp leaks a few bins into the
    # band and looks periodic.
    data = data - cv2.GaussianBlur(data, (0, 0), 2.0)
    contrast = float(data.std())
    data -= float(data.mean())
    window = cv2.createHanningWindow((w, h), cv2.CV_32F)
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(data * window)))
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    radius = np.hypot((yy - cy) / max(h / 2.0, 1.0), (xx - cx) / max(w / 2.0, 1.0))
    band = (radius > 0.10) & (radius < 0.85)
    axis = (np.abs(xx - cx) <= 2) | (np.abs(yy - cy) <= 2)
    region = spectrum[band & ~axis]
    if region.size < 512:
        return None

    mean = float(region.mean())
    peak_ratio = float(region.max()) / max(mean, 1e-6)
    k = max(12, region.size // 300)
    top = np.partition(region, -k)[-k:]
    periodic_energy = float(top.sum()) / max(float(region.sum()), 1e-6)
    return TextureSample(peak_ratio, periodic_energy, contrast, native)


def texture_reading(sample: TextureSample | None) -> CueReading:
    if sample is None:
        return CueReading.none()
    peak = _ramp(sample.peak_ratio, 14.0, 48.0)
    periodic = _ramp(sample.periodic_energy, 0.03, 0.12)
    # A periodic pattern worth convicting has to be visible, not quantisation
    # noise left behind by a smooth gradient.
    visible = _ramp(sample.contrast, 1.2, 3.0)
    level = min(peak, periodic) * visible
    confidence = _ramp(sample.crop_size, 90.0, 190.0)
    return CueReading(level, confidence)


# --------------------------------------------------------------------------- #
# confirm cue: blink, from pixel darkness inside the aligned face
# --------------------------------------------------------------------------- #
# Fixed ArcFace template positions (112x112). SCRFD gives five points and no
# eyelids, and the published 106-point index tables are unreliable, so the eye
# patches come from geometry we already trust: the aligned crop itself.
EYE_CENTERS_112 = ((38.29, 51.70), (73.53, 51.50))
EYE_HALF_112 = (10.0, 6.0)
# Forehead and both cheeks: skin reference, well clear of brows and mouth.
SKIN_PATCHES_112 = ((24.0, 82.0, 12.0, 8.0),
                    (88.0, 82.0, 12.0, 8.0),
                    (56.0, 24.0, 16.0, 7.0))

MIN_OPENNESS_CONFIDENCE = 0.35
MIN_SCREEN_CONFIDENCE = 0.35
MIN_TEXTURE_CONFIDENCE = 0.35


@dataclass
class EyeSample:
    openness: float       # share of eye-patch pixels clearly darker than skin
    confidence: float


@dataclass(frozen=True)
class HeadPoseSample:
    """Eye-axis nose offset normalized by eye spacing; not physical yaw."""

    yaw: float
    confidence: float


def extract_head_pose_sample(landmarks, *, mirrored: bool = False) -> HeadPoseSample | None:
    """Estimate a relative yaw proxy from SCRFD's five facial landmarks.

    The signed nose offset is projected onto the eye axis, which removes face
    translation and in-plane roll, then normalized by interocular distance.
    It is suitable for guiding a temporal head-turn challenge, not as a metric
    pose estimate or proof of physical presence.
    """
    try:
        points = np.asarray(landmarks, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
        return None
    if not np.isfinite(points[:3]).all():
        return None
    left_eye, right_eye, nose = points[:3]
    eye_vector = right_eye - left_eye
    eye_width = float(np.linalg.norm(eye_vector))
    if eye_width < 18.0:
        return None
    eye_axis = eye_vector / eye_width
    eye_center = (left_eye + right_eye) * 0.5
    nose_offset = float(np.dot(nose - eye_center, eye_axis)) / eye_width
    if mirrored:
        nose_offset = -nose_offset
    if not np.isfinite(nose_offset) or abs(nose_offset) > 0.45:
        return None
    return HeadPoseSample(
        nose_offset,
        _ramp(eye_width, 18.0, 42.0),
    )


class HeadTurnTracker:
    """Require forward → left → right → forward pose changes within one scan."""

    def __init__(self):
        self._baseline: list[float] = []
        self._phase_samples = 0
        self._baseline_yaw = 0.0
        self.confirmed = False
        self.phase = "baseline"

    def reset(self) -> None:
        self._baseline.clear()
        self._phase_samples = 0
        self._baseline_yaw = 0.0
        self.confirmed = False
        self.phase = "baseline"

    def observe(self, sample: HeadPoseSample | None) -> CueReading:
        if self.confirmed:
            return CueReading(1.0, 1.0)
        if (sample is None or not np.isfinite(sample.yaw) or
                not np.isfinite(sample.confidence) or
                sample.confidence < MIN_HEAD_POSE_CONFIDENCE):
            self.reset()
            self.phase = "quality"
            return CueReading.none()

        if self.phase == "baseline":
            self._baseline.append(float(sample.yaw))
            if len(self._baseline) >= HEAD_TURN_BASELINE_SAMPLES:
                self._baseline_yaw = float(np.median(self._baseline))
                if abs(self._baseline_yaw) > HEAD_TURN_MAX_BASELINE_OFFSET:
                    self._baseline.clear()
                    return CueReading.none()
                self.phase = "left"
            return CueReading.none()

        delta = float(sample.yaw) - self._baseline_yaw
        if self.phase == "left":
            self._phase_samples = self._phase_samples + 1 if delta <= -HEAD_TURN_MIN_DELTA else 0
            if self._phase_samples >= HEAD_TURN_SAMPLES:
                self.phase = "right"
                self._phase_samples = 0
        elif self.phase == "right":
            self._phase_samples = self._phase_samples + 1 if delta >= HEAD_TURN_MIN_DELTA else 0
            if self._phase_samples >= HEAD_TURN_SAMPLES:
                self.phase = "return"
                self._phase_samples = 0
        elif self.phase == "return":
            self._phase_samples = (self._phase_samples + 1
                                   if abs(delta) <= HEAD_TURN_CENTER_TOLERANCE else 0)
            if self._phase_samples >= HEAD_TURN_RETURN_SAMPLES:
                self.confirmed = True
                self.phase = "complete"
                return CueReading(1.0, 1.0)
        return CueReading.none()


def _patch(gray: np.ndarray, width: int, height: int,
           cx: float, cy: float, half_w: float, half_h: float) -> np.ndarray:
    sx, sy = width / 112.0, height / 112.0
    x1 = int(round((cx - half_w) * sx))
    x2 = int(round((cx + half_w) * sx))
    y1 = int(round((cy - half_h) * sy))
    y2 = int(round((cy + half_h) * sy))
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return np.empty(0, dtype=np.float32)
    return gray[y1:y2, x1:x2].reshape(-1)


def eye_openness(aligned_bgr: np.ndarray, interocular_px: float) -> EyeSample | None:
    """Bilateral eye darkness relative to the surrounding skin.

    An open eye puts a dark iris and bright sclera inside the patch; a closed
    eye is mostly skin. Both eyes are averaged; this heuristic can still be
    affected by alignment jitter or illumination changes.
    """
    if aligned_bgr is None or aligned_bgr.size == 0:
        return None
    h, w = aligned_bgr.shape[:2]
    if min(h, w) < 64:
        return None
    gray = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)

    skin = np.concatenate([
        _patch(gray, w, h, cx, cy, hw, hh) for cx, cy, hw, hh in SKIN_PATCHES_112
    ])
    if skin.size < 40:
        return None
    skin_mean = float(skin.mean())
    skin_std = float(skin.std())
    dark_floor = skin_mean - max(1.25 * skin_std, 12.0)

    shares = []
    for cx, cy in EYE_CENTERS_112:
        patch = _patch(gray, w, h, cx, cy, EYE_HALF_112[0], EYE_HALF_112[1])
        if patch.size < 20:
            return None
        shares.append(float((patch < dark_floor).mean()))
    return EyeSample(openness=float(np.mean(shares)),
                     confidence=_ramp(float(interocular_px), 18.0, 45.0))


def blink_reading(openness_history: list[float],
                  min_absolute_drop: float = 0.012) -> CueReading:
    """Dip-and-recovery in estimated eye openness, not a physical-presence proof.

    Needs a dip below 0.62 of baseline, an absolute change big enough that
    patch jitter cannot produce it, and recovery above 0.82 within three
    frames on both sides of the dip. Absence of a blink is an abstention, never
    a failure - a live person may not blink during a short window.
    """
    if not openness_history or len(openness_history) < 4:
        return CueReading.none()
    recent = [float(v) for v in openness_history[-10:]]
    baseline = float(np.percentile(recent, 90))
    if baseline <= 1e-6:
        return CueReading.none()
    normalised = [value / baseline for value in recent]
    index = int(np.argmin(normalised))
    if normalised[index] >= 0.62:
        return CueReading.none()
    required_drop = max(min_absolute_drop, 0.20 * baseline)
    if baseline - recent[index] < required_drop:
        return CueReading.none()
    if index <= 0 or index >= len(normalised) - 1:
        return CueReading.none()

    radius = 3
    before = normalised[max(0, index - radius):index]
    after = normalised[index + 1:index + 1 + radius]
    if any(value > 0.82 for value in before) and any(value > 0.82 for value in after):
        return CueReading(1.0, 1.0)
    return CueReading.none()


# --------------------------------------------------------------------------- #
# confirm cue: rigid-invariant micro-motion
# --------------------------------------------------------------------------- #
def interocular_distance(landmarks) -> float:
    try:
        pts = np.asarray(landmarks, dtype=np.float32)
    except (TypeError, ValueError):
        return 0.0
    if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] != 2:
        return 0.0
    return float(np.linalg.norm(pts[1] - pts[0]))


def landmark_ratios(landmarks) -> np.ndarray | None:
    """Five landmark distances divided by interocular distance.

    Every term is a ratio of distances, so translation, rotation and uniform
    scale cancel. Perspective changes and detector jitter do not cancel, so
    these ratios must not be used to authorize an unlock.
    """
    try:
        pts = np.asarray(landmarks, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if pts.ndim != 2 or pts.shape[0] < 5 or pts.shape[1] != 2:
        return None
    left_eye, right_eye, nose, left_mouth, right_mouth = pts[:5]
    eye = float(np.linalg.norm(right_eye - left_eye))
    if eye < 1e-3:
        return None
    mouth_centre = (left_mouth + right_mouth) / 2.0
    return np.array([
        np.linalg.norm(nose - left_eye) / eye,
        np.linalg.norm(nose - right_eye) / eye,
        np.linalg.norm(left_mouth - right_mouth) / eye,
        np.linalg.norm(nose - mouth_centre) / eye,
        np.linalg.norm(left_mouth - left_eye) / eye,
    ], dtype=np.float32)


def micro_motion_reading(ratio_history: list[np.ndarray] | None,
                         interocular_px: float = 0.0) -> CueReading:
    rows = [row for row in (ratio_history or [])[-12:] if row is not None]
    if len(rows) < 5:
        return CueReading.none()
    array = np.stack(rows).astype(np.float32)
    median = np.median(array, axis=0)
    # Three-frame smoothing turns detector jitter into noise that averages
    # down, while a real expression or head movement survives.
    if len(array) >= 5:
        kernel = np.ones(3, dtype=np.float32) / 3.0
        smooth = np.stack([
            np.convolve(array[:, i], kernel, mode="valid") for i in range(array.shape[1])
        ], axis=1)
    else:
        smooth = array
    relative = np.ptp(smooth, axis=0) / np.maximum(np.abs(median), 1e-6)
    variation = float(np.max(relative))
    # Floor is deliberately above measured landmark jitter; a photo moved by
    # hand keeps these ratios constant because they are scale/pose invariant.
    level = _ramp(variation, 0.006, 0.020)
    confidence = _ramp(float(len(rows)), 5.0, 9.0) * _ramp(float(interocular_px), 18.0, 40.0)
    return CueReading(level, confidence)

# --------------------------------------------------------------------------- #
# per-frame readings
# --------------------------------------------------------------------------- #
def _gate(reading: CueReading, minimum: float) -> CueReading:
    """A cue that is not confident enough abstains; it never counts."""
    return reading if reading.confidence >= minimum else CueReading.none()


def readings(glare: GlareSample | None = None,
             device: DeviceObservation | None = None,
             openness_history: list[float] | None = None,
             screen: ScreenSample | None = None,
             texture: TextureSample | None = None,
             motion_history: list[np.ndarray] | None = None,
             interocular_px: float = 0.0,
              head_turn: CueReading | None = None) -> dict[Cue, CueReading]:
    return {
        Cue.GLOSS_GLARE: gloss_glare_reading(glare),
        Cue.SCREEN_GLOW: _gate(screen_glow_reading(screen), MIN_SCREEN_CONFIDENCE),
        Cue.DEVICE_DETECTED: device_reading(device) if device else CueReading.none(),
        Cue.PRINT_OR_MOIRE: _gate(texture_reading(texture), MIN_TEXTURE_CONFIDENCE),
        # Abstain: no head-yaw source on Windows. See the module docstring.
        Cue.FLAT_VS_3D: CueReading.none(),
        Cue.DEPTH_POSE: CueReading.none(),
        Cue.BLINK: blink_reading(openness_history or []),
        Cue.HEAD_TURN: head_turn or CueReading.none(),
        Cue.MICRO_MOTION: micro_motion_reading(motion_history, interocular_px),
    }
