"""Enrollment acceptance and approximate five-landmark pose coverage (not 3D)."""
import numpy as np

TARGET = 18
QUOTAS = {"front": 6, "left": 3, "right": 3, "up": 3, "down": 3}
PROMPTS = {"front": "Look straight ahead", "left": "Turn slightly to one side",
           "right": "Turn slightly to the other side", "up": "Tilt slightly up",
           "down": "Tilt slightly down"}


class EnrollmentSession:
    def __init__(self):
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

    def accept(self, embedding, pose):
        if self.complete:
            return False, "Capture complete"
        e = np.asarray(embedding, dtype=np.float32)
        if e.ndim != 1 or not np.isfinite(e).all() or np.linalg.norm(e) < 1e-6:
            return False, "Could not read the face; try again"
        if pose is None or not np.isfinite(pose).all():
            return False, "Keep both eyes, nose and mouth visible"
        e = e / np.linalg.norm(e)
        if self.samples:
            if e.shape != self.samples[0].shape:
                return False, "Face model changed; start a new capture"
            scores = np.asarray(self.samples) @ e
            if float(scores.max()) > .995:
                return False, "Sample too similar; change your angle slightly"
            if float(np.dot(self.samples[0], e)) < .45:
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
        self.samples.append(e.copy())
        self.coverage[bucket] += 1
        return True, self.prompt
