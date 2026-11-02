"""Compact capture cards and silent playback of the original Mac animation assets."""
import cv2
from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from ..paths import resolve_paths
from .enrollment import TARGET, quality_percent, quality_tier

_loader = None
_clips = None


def _cache_clips(clips):
    global _clips
    _clips = clips


def _stop_loader():
    if _loader is not None:
        _loader.requestInterruption()
        _loader.wait()


class AnimationLoader(QThread):
    loaded = Signal(object)

    def __init__(self, root, parent):
        super().__init__(parent)
        self.root = root

    def run(self):
        clips = {}
        for state, filename in (("success", "unlockanimation.mp4"),
                                ("failure", "unsuccessfulunlockanimation.mp4")):
            cap = cv2.VideoCapture(str(self.root / filename))
            frames = []
            fps = cap.get(cv2.CAP_PROP_FPS) or 60
            try:
                while len(frames) < 180 and not self.isInterruptionRequested():
                    ok, frame = cap.read()
                    if not ok:
                        break
                    frame = cv2.resize(frame, (160, 160), interpolation=cv2.INTER_AREA)
                    frames.append(QImage(frame.data, 160, 160, frame.strides[0], QImage.Format_BGR888).copy())
            finally:
                cap.release()
            clips[state] = (frames, fps)
        self.loaded.emit(clips)


class ScanAnimation(QWidget):
    """One-shot native-timing playback; holds the final frame and never plays sound."""
    def __init__(self, parent=None, size=80, art=64, radius=16):
        super().__init__(parent)
        self._art = art
        self._radius = radius
        self.setFixedSize(size, size)
        self.setAccessibleName("Face capture status")
        paths = resolve_paths()
        root = (paths.executable_dir if paths.packaged else paths.project_root) / "assets" / "animations"
        self.still = QImage(str(root / "unlockstatic.png"))
        self.current_frame = QImage()
        self.clips = {}
        self.state = "idle"
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._frame)
        self.root = root
        self._load_connected = False
        self.show_state("idle")

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("black"))
        painter.drawRoundedRect(self.rect(), self._radius, self._radius)
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(self.rect(), self._radius, self._radius)
        painter.setClipPath(path)
        image = self.current_frame if not self.current_frame.isNull() else self.still
        if not image.isNull():
            side = min(self._art, self.width(), self.height())
            scaled = image.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawImage((self.width() - scaled.width()) / 2,
                              (self.height() - scaled.height()) / 2, scaled)
        else:
            painter.setPen(QColor("#dcecff"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             {"success": "✓", "failure": "Retry"}.get(self.state, "Scanning"))
        painter.restore()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if _clips is not None:
            self.clips = _clips
            self.show_state(self.state)
            return
        global _loader
        app = QApplication.instance()
        if _loader is None:
            # Parent to QApplication and stop it on aboutToQuit; a hidden or
            # unshown page must not create a running native thread during tests.
            _loader = AnimationLoader(self.root, app)
            _loader.loaded.connect(_cache_clips)
            app.aboutToQuit.connect(_stop_loader)
        if not self._load_connected:
            _loader.loaded.connect(self._loaded)
            self._load_connected = True
        if not _loader.isRunning() and not _loader.isFinished():
            _loader.start()

    def _loaded(self, clips):
        self.clips = clips
        self.show_state(self.state)

    def show_state(self, state):
        self.state = state
        self.timer.stop()
        frames, _ = self.clips.get(state, ([], 60))
        if not frames:
            self.current_frame = QImage()
            self.update()
            return
        self.clock.start()
        self._frame()
        self.timer.start()

    def _frame(self):
        frames, fps = self.clips.get(self.state, ([], 60))
        if not frames:
            self.timer.stop()
            return
        index = min(len(frames) - 1, int(self.clock.elapsed() * fps / 1000))
        self.current_frame = frames[index]
        self.update()
        if index == len(frames) - 1:
            self.timer.stop()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


_TIER_COLORS = {
    "poor": QColor(255, 107, 107),
    "fair": QColor(230, 185, 92),
    "good": QColor(94, 220, 120),
}


class CaptureBars(QWidget):
    """One colored tick per sample slot; unrated ticks are hollow/dim."""

    def __init__(self, qualities=None, parent=None):
        super().__init__(parent)
        self.qualities = list(qualities or [])
        self.setFixedHeight(26)
        self.setMinimumWidth(155)

    def set_samples(self, qualities):
        self.qualities = list(qualities or [])
        self.setAccessibleName(f"{len(self.qualities)} of {TARGET} samples")
        self.update()

    def set_count(self, count):
        self.set_samples([None] * max(0, min(TARGET, count)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        step = min(9, self.width() / TARGET)
        for i in range(TARGET):
            rect = QRectF(i * step, 3, 3.5, 21)
            if i < len(self.qualities):
                tier = quality_tier(self.qualities[i])
                color = _TIER_COLORS.get(tier, QColor(150, 165, 190))
                color.setAlpha(255 if tier else 110)
                p.setBrush(color)
                p.drawRoundedRect(rect, 1.7, 1.7)
            else:
                p.setBrush(QColor(255, 255, 255, 22))
                p.drawRoundedRect(rect, 1.7, 1.7)


class IdentityCaptureCard(QFrame):
    def __init__(self, name, samples=None, parent=None):
        super().__init__(parent)
        self.setObjectName("identityCaptureCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 9)
        layout.setSpacing(3)
        self.name = QLabel(name)
        self.name.setObjectName("identityName")
        layout.addWidget(self.name, 0, Qt.AlignLeft)
        self.detail = QLabel()
        self.detail.setObjectName("identityDetail")
        layout.addWidget(self.detail)
        self.bars = CaptureBars()
        layout.addWidget(self.bars)
        self.set_samples(samples or [])

    def set_samples(self, qualities):
        self.bars.set_samples(qualities)
        total = len(qualities)
        percent = quality_percent(qualities)
        if percent is None:
            self.detail.setText(f"Capture quality · {total}/{TARGET} not recorded")
            self.setToolTip(
                "Samples saved before capture quality existed. "
                "Capture quality is not a measure of authentication security."
            )
        else:
            self.detail.setText(f"Capture quality • {percent}% · {total}/{TARGET}")
            self.setToolTip(
                "Capture quality at capture time (face size, sharpness, "
                "lighting). Not a measure of authentication security."
            )
