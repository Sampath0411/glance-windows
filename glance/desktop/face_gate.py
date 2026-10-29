"""Centered in-app face check shown before sensitive settings pages.

The gate is deliberately quiet: it never displays camera frames, only the same
face animation used during enrollment. A page unlocks after three consecutive
matches at the saved threshold, and a missing camera or missing models always
fails closed.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import backend
from .capture_widgets import ScanAnimation
from .theme import ACCENT, DANGER

SCAN_SECONDS = 20.0
REQUIRED_MATCHES = 3
SUCCESS_HOLD_MS = 1100


class FaceGateDialog(QDialog):
    """Block a page until an enrolled face has been matched three times."""

    def __init__(self, parent, purpose: str, camera=None, *,
                 threshold: float | None = None, acquire=None, release=None,
                 restart=None):
        super().__init__(parent)
        self.purpose = purpose
        self.camera = camera if camera is not None else parent.camera
        if threshold is None:
            settings = getattr(parent, "settings", None)
            threshold = getattr(settings, "threshold", 0.60)
        self.threshold = float(threshold)
        self._acquire = acquire or (lambda: parent.acquire_camera(self.OWNER))
        self._release = release or (lambda: parent.release_camera(self.OWNER))
        self._restart = restart or getattr(parent, "retry_camera", None)

        self._scanning = False
        self._started = False
        self._shut = False
        self._holding_camera = False
        self._streak_name = ""
        self._streak = 0
        self.matched_name = ""

        self.setWindowTitle("Face check")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setAccessibleName(f"Face check before {purpose}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 22)
        layout.setSpacing(12)

        heading = QLabel("Face check")
        heading.setObjectName("h1")
        heading.setAlignment(Qt.AlignHCenter)
        layout.addWidget(heading)

        self.purpose_label = QLabel(f"Confirm it's you before opening {purpose}.")
        self.purpose_label.setObjectName("muted")
        self.purpose_label.setWordWrap(True)
        self.purpose_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.purpose_label)

        self.animation = ScanAnimation(size=148, art=112, radius=34)
        layout.addWidget(self.animation, 0, Qt.AlignHCenter)

        self.status = QLabel("Looking for your face…")
        self.status.setObjectName("body")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.status)

        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.retry = QPushButton("Retry")
        self.retry.setObjectName("primary")
        self.retry.clicked.connect(self._retry)
        self.retry.hide()
        buttons.addWidget(self.retry)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        layout.addLayout(buttons)

        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(int(SCAN_SECONDS * 1000))
        self._scan_timer.timeout.connect(self._timed_out)

        self._accept_timer = QTimer(self)
        self._accept_timer.setSingleShot(True)
        self._accept_timer.timeout.connect(self.accept)

        self.camera.gate.connect(self._on_gate)
        self.camera.failed.connect(self._on_camera_failed)

    OWNER = "face-gate"

    # -- lifecycle --------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._center_on_parent()
        if not self._started:
            self._started = True
            self._start_scan()

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        host = parent.window()
        if host is None or not host.isVisible():
            return
        self.move(host.frameGeometry().center() - self.rect().center())

    def done(self, result: int) -> None:
        self._shutdown()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        self._scan_timer.stop()
        self._accept_timer.stop()
        self._scanning = False
        if self._holding_camera:
            self._holding_camera = False
            try:
                self.camera.set_gate(False)
            finally:
                self._release()
        for signal, slot in ((self.camera.gate, self._on_gate),
                             (self.camera.failed, self._on_camera_failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # -- scanning ---------------------------------------------------------
    def _start_scan(self) -> None:
        self._streak_name = ""
        self._streak = 0
        self.matched_name = ""
        self._scanning = False
        self._accept_timer.stop()
        self.retry.hide()
        self.cancel.setEnabled(True)
        self.animation.show_state("idle")
        self.status.setStyleSheet("")
        self.hint.setText("")

        if not backend.identity_summary(0):
            self._fail("No faces are enrolled on this PC. Enroll a face first.",
                       retry=False)
            return
        if not backend.models_ready():
            self._fail("Face models are not installed. Download them in About.",
                       retry=False)
            return

        self._scanning = True
        self.status.setText("Looking for your face…")
        self._hold_camera()
        self.camera.set_gate(True)
        self._scan_timer.start()

    def _hold_camera(self) -> None:
        if not self._holding_camera:
            self._holding_camera = True
            self._acquire()

    def _retry(self) -> None:
        self.retry.hide()
        if self._restart is not None:
            self._restart(self.OWNER)
        self._start_scan()

    def _timed_out(self) -> None:
        if self._scanning:
            self._fail("We couldn't verify your face. Try again or cancel.")

    def _on_gate(self, name: str, score: float, hint: str) -> None:
        if not self._scanning:
            return
        if name and score >= self.threshold:
            if name == self._streak_name:
                self._streak += 1
            else:
                self._streak_name = name
                self._streak = 1
            self.status.setText("Face recognized · hold still")
            self.status.setStyleSheet(f"color: {ACCENT};")
            self.hint.setText("")
            if self._streak >= REQUIRED_MATCHES:
                self._succeed(name)
            return

        self._streak_name = ""
        self._streak = 0
        self.status.setText("Looking for your face…")
        self.status.setStyleSheet("")
        self.hint.setText(hint or "Center your face in view")

    def _on_camera_failed(self, message: str) -> None:
        if self._scanning:
            self._fail(f"The camera is unavailable. {message}")

    def _succeed(self, name: str) -> None:
        self._scanning = False
        self._scan_timer.stop()
        self.matched_name = name
        self.animation.show_state("success")
        self.status.setText("Verified · opening")
        self.status.setStyleSheet(f"color: {ACCENT};")
        self.hint.setText("")
        self.retry.hide()
        self.cancel.setEnabled(False)
        self._stop_gate()
        self._accept_timer.start(SUCCESS_HOLD_MS)

    def _fail(self, message: str, *, retry: bool = True) -> None:
        self._scanning = False
        self._scan_timer.stop()
        self.animation.show_state("failure")
        self.status.setText(message)
        self.status.setStyleSheet(f"color: {DANGER};")
        self.hint.setText("Keep your face clearly visible and try again." if retry else "")
        self.retry.setVisible(retry)
        self.cancel.setEnabled(True)
        self._stop_gate()

    def _stop_gate(self) -> None:
        if self._holding_camera:
            self._holding_camera = False
            try:
                self.camera.set_gate(False)
            finally:
                self._release()

    # -- state for tests and callers -------------------------------------
    def is_scanning(self) -> bool:
        return self._scanning

    def streak(self) -> int:
        return self._streak

    def retry_visible(self) -> bool:
        return not self.retry.isHidden()
