"""Compact first-run setup guide for Glance on Windows."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class FirstRunDialog(QDialog):
    """A short setup guide; security-sensitive actions stay in existing pages."""

    COMPLETED_KEY = "onboarding/completed"

    def __init__(self, main_window, settings: QSettings | None = None):
        super().__init__(main_window)
        self.main_window = main_window
        self.settings = settings or QSettings()
        self.setWindowTitle("Glance Setup")
        self.setFixedSize(420, 390)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        brand = QLabel("Glance")
        brand.setObjectName("big")
        root.addWidget(brand)
        self.progress = QLabel("Step 1 of 5")
        self.progress.setObjectName("muted")
        root.addWidget(self.progress)

        self.stack = QStackedWidget()
        self._add_step("Face Unlock for Windows", "Set up face recognition and choose which Windows account Glance may unlock.")
        self._add_step("Camera access", "Allow desktop apps to access your camera in Windows Settings. Glance only opens the camera for enrollment or when you explicitly turn on preview.", "Open camera privacy settings", self._open_camera_privacy)
        self._add_step("Enroll your face", "Capture several samples in the Face tab. Enrollment is stored locally. Windows currently authorizes one selected identity at a time.", "Open Face", lambda: self._open_page(1))
        self._add_step("Windows account password", "Windows requires a valid account password after a face match. Set or change it in the Password tab; it is verified and stored encrypted for this PC.", "Open Password", lambda: self._open_page(2))
        self._add_step("Security note", "This is a convenience prototype using a standard webcam, not Windows Hello or Apple Face ID. The normal Windows password/PIN sign-in remains available.")
        root.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        self.skip = QPushButton("Skip for now")
        self.skip.clicked.connect(self.reject)
        footer.addWidget(self.skip)
        footer.addStretch(1)
        self.back = QPushButton("Back")
        self.back.clicked.connect(self._back)
        footer.addWidget(self.back)
        self.next = QPushButton("Next")
        self.next.setObjectName("primary")
        self.next.clicked.connect(self._next)
        footer.addWidget(self.next)
        root.addLayout(footer)
        self._update_step()

    @classmethod
    def is_completed(cls, settings: QSettings | None = None) -> bool:
        settings = settings or QSettings()
        return settings.value(cls.COMPLETED_KEY, False, type=bool)

    def _add_step(self, title: str, detail: str, action_title: str | None = None,
                  action=None) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("h1")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        body = QLabel(detail)
        body.setObjectName("muted")
        body.setWordWrap(True)
        layout.addWidget(body)
        if action_title and action:
            button = QPushButton(action_title)
            button.clicked.connect(action)
            layout.addWidget(button, 0, Qt.AlignLeft)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def _open_camera_privacy(self) -> None:
        webbrowser.open("ms-settings:privacy-webcam")

    def _open_page(self, index: int) -> None:
        self.main_window._navigate(index)
        self.settings.setValue(self.COMPLETED_KEY, True)
        self.accept()

    def _back(self) -> None:
        if self.stack.currentIndex() > 0:
            self.stack.setCurrentIndex(self.stack.currentIndex() - 1)
            self._update_step()

    def _next(self) -> None:
        if self.stack.currentIndex() + 1 < self.stack.count():
            self.stack.setCurrentIndex(self.stack.currentIndex() + 1)
            self._update_step()
            return
        self.settings.setValue(self.COMPLETED_KEY, True)
        self.accept()

    def _update_step(self) -> None:
        index = self.stack.currentIndex()
        self.progress.setText(f"Step {index + 1} of {self.stack.count()}")
        self.back.setEnabled(index > 0)
        self.next.setText("Finish" if index == self.stack.count() - 1 else "Next")
