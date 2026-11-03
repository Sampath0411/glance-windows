"""Enrollment acceptance and approximate five-landmark pose coverage (not 3D)."""
import numpy as np

TARGET = 18
QUOTAS = {"front": 6, "left": 3, "right": 3, "up": 3, "down": 3}
PROMPTS = {"front": "Look straight ahead", "left": "Turn slightly to one side",
           "right": "Turn slightly to the other side", "up": "Tilt slightly up",
           "down": "Tilt slightly down"}

# Capture-quality tiers, matching the Mac tiers (poor < 0.40, fair < 0.50,
# good >= 0.50). Unrated samples (quality None) are never counted as poor.
QUALITY_POOR = 0.40
QUALITY_FAIR = 0.50


def quality_tier(quality: float | None) -> str | None:
    """Return 'poor', 'fair', 'good', or None when the sample is unrated."""
    if quality is None:
        return None
    if quality < QUALITY_POOR:
        return "poor"
    if quality < QUALITY_FAIR:
        return "fair"
    return "good"


def quality_percent(qualities) -> int | None:
    """Share of samples that are NOT poor, as a rounded percentage (Mac formula).

    ``(total - poorCount) / total * 100``. Unrated samples are included in
    the total and never counted as poor; with no rated samples there is no
    percentage to report.
    """
    values = list(qualities)
    if not any(quality is not None for quality in values):
        return None
    total = len(values)
    poor = sum(quality is not None and quality < QUALITY_POOR for quality in values)
    return int(round((total - poor) / total * 100))


class EnrollmentSession:
    def __init__(self):
        # Each sample is (embedding, quality|None); quality is the Windows
        # capture-quality score recorded at capture time.
        self.samples = []
        self.coverage = dict.fromkeys(QUOTAS, 0)
        self.front_pitch = None

    @property
    def complete(self):
        return len(self.samples) >= TARGET

    @property
    def prompt(self):
        for key, quota in QUOTAS.items():
            if self.coverage[key] < quota:
                return PROMPTS[key] + " · hold still, then vary the angle gently"
        return "Capture complete"

    @property
    def qualities(self):
        return [quality for _, quality in self.samples]

    def accept(self, embedding, pose, quality: float | None = None):
        if self.complete:
            return False, "Capture complete"
        e = np.asarray(embedding, dtype=np.float32)
        if e.ndim != 1 or not np.isfinite(e).all() or np.linalg.norm(e) < 1e-6:
            return False, "Could not read the face; try again"
        if pose is None or not np.isfinite(pose).all():
            return False, "Keep both eyes, nose and mouth visible"
        e = e / np.linalg.norm(e)
        if self.samples:
            if e.shape != self.samples[0][0].shape:
                return False, "Face model changed; start a new capture"
            scores = np.asarray([s[0] for s in self.samples]) @ e
            if float(scores.max()) > .995:
                return False, "Sample too similar; change your angle slightly"
            if float(np.dot(self.samples[0][0], e)) < .45:
                return False, "Keep the same person in view throughout capture"
        yaw, pitch = pose
        if self.front_pitch is None:
            if abs(yaw) > .12:
                return False, "Look straight ahead for the first sample"
            self.front_pitch = pitch
        delta = pitch - self.front_pitch
        bucket = ("left" if yaw < -.12 else "right" if yaw > .12 else
                  "up" if delta < -.09 else "down" if delta > .09 else "front")
        if self.coverage[bucket] >= QUOTAS[bucket]:
            return False, self.prompt
        self.samples.append((e.copy(), quality))
        self.coverage[bucket] += 1
        return True, self.prompt
