"""Short, bounded startup splash using the application's packaged icon."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QSplashScreen, QWidget

from .theme import app_icon


class StartupSplash(QSplashScreen):
    MAX_VISIBLE_MS = 1400

    def __init__(self) -> None:
        pixmap = app_icon().pixmap(128, 128)
        super().__init__(pixmap, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Glance")
        self._finished = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self.close)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._timeout.isActive() and not self._finished:
            self._timeout.start(self.MAX_VISIBLE_MS)

    def finish(self, main_window: QWidget) -> None:
        if self._finished:
            return
        self._finished = True
        self._timeout.stop()
        super().finish(main_window)

    def close(self) -> bool:
        self._timeout.stop()
        self._finished = True
        return super().close()
