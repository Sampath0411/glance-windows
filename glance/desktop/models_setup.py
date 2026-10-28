"""First-run download of the InsightFace models that Glance does not ship."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import MODEL_TERMS_URL
from . import backend


class _DownloadWorker(QThread):
    progressed = Signal(int)
    completed = Signal(bool, str)

    def run(self) -> None:  # noqa: D401 - Qt entry point
        ok, message = backend.install_models(
            progress=lambda value: self.progressed.emit(
                max(0, min(100, int(round(value * 100))))
            )
        )
        self.completed.emit(ok, message)


class ModelsDialog(QDialog):
    """Explain the upstream terms, then fetch and verify the two model files."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Glance face models")
        self.setMinimumWidth(500)
        self.setModal(True)
        self._worker: _DownloadWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)

        heading = QLabel("Download face models")
        heading.setObjectName("big")
        layout.addWidget(heading)

        body = QLabel(
            "Face detection and recognition need two InsightFace models. They are not "
            "bundled with the installer, so Glance downloads them once from InsightFace's "
            "official release and checks the SHA-256 before use."
        )
        body.setObjectName("body")
        body.setWordWrap(True)
        layout.addWidget(body)

        terms = QLabel(
            "The buffalo_s weights are licensed by InsightFace for non-commercial "
            "research use. Glance's MIT license does not cover them, and downloading "
            "them means accepting InsightFace's terms."
        )
        terms.setObjectName("muted")
        terms.setWordWrap(True)
        layout.addWidget(terms)

        links = QHBoxLayout()
        self.terms_button = QPushButton("Read InsightFace terms")
        self.terms_button.setFlat(True)
        self.terms_button.clicked.connect(lambda: webbrowser.open(MODEL_TERMS_URL))
        links.addWidget(self.terms_button, 0, Qt.AlignLeft)
        links.addStretch(1)
        layout.addLayout(links)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.later = QPushButton("Not now")
        self.later.clicked.connect(self.reject)
        buttons.addWidget(self.later)
        buttons.addStretch(1)
        self.download = QPushButton("Download models")
        self.download.setObjectName("primary")
        self.download.clicked.connect(self.start_download)
        buttons.addWidget(self.download)
        layout.addLayout(buttons)

    def start_download(self) -> None:
        if self._worker is not None:
            return
        self.download.setEnabled(False)
        self.later.setEnabled(False)
        self.terms_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.status.setVisible(True)
        self.status.setText("Connecting to GitHub…")

        worker = _DownloadWorker(self)
        worker.progressed.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_progress(self, percent: int) -> None:
        self.progress.setValue(percent)
        self.status.setText(
            "Installing…" if percent >= 99 else f"Downloading… {percent}%"
        )

    def _on_completed(self, ok: bool, message: str) -> None:
        self._worker = None
        self.progress.setVisible(not ok)
        self.terms_button.setEnabled(True)
        self.later.setEnabled(True)
        if ok:
            self.progress.setValue(100)
            self.status.setText("Models installed. You can enroll a face now.")
            self.download.setText("Close")
            self.download.setEnabled(True)
            try:
                self.download.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.download.clicked.connect(self.accept)
            return
        self.status.setText(message)
        self.download.setText("Try again")
        self.download.setEnabled(True)

    def reject(self) -> None:  # noqa: D401 - Qt override
        if self._worker is not None:
            return  # keep the download alive; it installs per-file and is safe
        super().reject()

    @classmethod
    def ensure_models(cls, parent: QWidget | None = None) -> bool:
        """Show the dialog only when the models are missing."""
        if backend.models_ready():
            return True
        cls(parent).exec()
        return backend.models_ready()
