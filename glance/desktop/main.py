"""Glance desktop window: sidebar, pages, tray."""

from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import backend
from .camera import CameraWorker
from .models_setup import ModelsDialog
from .onboarding import FirstRunDialog
from .pages import (
    AboutPage, CameraPage, Dashboard, EnrollPage, PasswordDialog,
    PasswordPage, RecognitionPage,
)
from .settings_widgets import FloatingTabBar
from .navigation import Sidebar, style_native_window
from .theme import ACCENT, MUTED, Dot, app_icon, resolve_theme, windows_appearance

APP_MUTEX = "Local\\GlanceDesktop"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glance")
        self.setWindowIcon(app_icon())
        self._compact_size = (1040, 760)
        self._enrollment_size = self._compact_size
        self.resize(*self._compact_size)
        self.setMinimumSize(960, 700)

        self.settings = backend.Settings.load()
        self._owners: set[str] = set()
        self._quitting = False
        self._camera_stop_timer = QTimer(self)
        self._camera_stop_timer.setSingleShot(True)
        self._camera_stop_timer.setInterval(250)
        self._camera_stop_timer.timeout.connect(self._stop_camera_if_unowned)

        self.camera = CameraWorker(self.settings.camera, self.settings.threshold, self)
        self.camera.failed.connect(self._camera_failed)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.dashboard = Dashboard(self)
        self.enroll = EnrollPage(self)
        self.password_page = PasswordPage(self)
        self.camera_page = CameraPage(self)
        self.settings_page = RecognitionPage(self)
        self.about_page = AboutPage(self)
        self.logs = self.about_page.logs
        self.pages = (
            self.dashboard, self.enroll, self.password_page,
            self.camera_page, self.settings_page, self.about_page,
        )
        for page in self.pages:
            if page in (self.password_page, self.camera_page, self.about_page):
                page.layout().setContentsMargins(32, 28, 32, 28)
                page.layout().setSpacing(20)
            self.stack.addWidget(page)
        self.nav = Sidebar(
            ("Overview", "Your face", "Windows sign-in", "Camera", "Recognition", "About"), self
        )
        self.nav.indexChanged.connect(self._navigate)
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        style_native_window(self)

        self.chip_dot = Dot(MUTED)
        self.chip_label = QLabel("checking…")
        self.camera.ready.connect(self.enroll.on_camera_ready)
        self.camera.preview.connect(self.dashboard.on_preview)
        self.camera.preview.connect(self.enroll.on_preview)
        self.camera.preview.connect(self.camera_page.on_preview)
        self.camera.captured.connect(self.enroll.on_capture)

        self._tray: QSystemTrayIcon | None = None
        self._chip_timer = QTimer(self)
        self._chip_timer.setInterval(3000)
        self._chip_timer.timeout.connect(self._refresh_chip)
        self._chip_timer.start()
        self._refresh_chip()

    # -- camera sharing ---------------------------------------------------
    def acquire_camera(self, owner: str) -> None:
        self._camera_stop_timer.stop()
        self._owners.add(owner)
        if not self.camera.isRunning():
            self.camera.start()

    def release_camera(self, owner: str) -> None:
        self._owners.discard(owner)
        if not self._owners:
            self._camera_stop_timer.start()

    def _stop_camera_if_unowned(self) -> None:
        if not self._owners and self.camera.isRunning():
            self.camera.stop()
            self.camera.wait(3000)

    def retry_camera(self, owner: str) -> None:
        self._camera_stop_timer.stop()
        self._owners.add(owner)
        if self.camera.isRunning():
            self.camera.stop()
            self.camera.wait(3000)
        if not self.camera.isRunning():
            self.camera.start()

    def _camera_failed(self, message: str) -> None:
        self.dashboard.preview.clear()
        self.enroll.on_camera_failed(message)
        self.camera_page.on_camera_failed(message)
        if self._tray is not None:
            self._tray.showMessage("Camera problem", message, QSystemTrayIcon.Warning, 6000)

    # -- navigation -------------------------------------------------------
    def _navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav.set_current_index(index)

    def show_first_run_if_needed(self) -> None:
        if not FirstRunDialog.is_completed():
            FirstRunDialog(self).exec()

    def startup_sequence(self) -> None:
        """Ask for the downloadable face models, then show the setup guide."""
        ModelsDialog.ensure_models(self)
        self.show_first_run_if_needed()

    # -- reactions to changes ---------------------------------------------
    def on_identities_changed(self, name: str) -> None:
        answer = QMessageBox.question(
            self, "Glance",
            f"Samples for {name} are saved.\n\nRestart protection so the agent reloads the "
            "enrolled faces now?",
        )
        if answer == QMessageBox.Yes:
            self.restart_agent()

    def settings_changed(self) -> None:
        if not backend.agent_running():
            return
        answer = QMessageBox.question(
            self, "Glance",
            "Settings saved. Restart protection to apply them now?",
        )
        if answer == QMessageBox.Yes:
            self.restart_agent()

    def restart_agent(self) -> None:
        if backend.agent_running():
            ok, message = backend.stop_agent()
            if not ok:
                QMessageBox.warning(self, "Glance", message)
                return
            QTimer.singleShot(400, self._start_agent_again)
        else:
            self._start_agent_again()

    def _start_agent_again(self) -> None:
        ok, message = backend.start_agent()
        if not ok:
            QMessageBox.warning(self, "Glance", message)
        self._refresh_chip()

    # -- tray -------------------------------------------------------------
    def attach_tray(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray

    def _refresh_chip(self) -> None:
        running = backend.agent_running()
        self.chip_dot.set_color(ACCENT if running else "#e3b341")
        self.chip_label.setText("protected" if running else "paused")
        if self._tray is not None:
            self._tray.setToolTip("Glance · protected" if running else "Glance · paused")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quitting or self._tray is None or not self._tray.isVisible():
            self.camera.stop()
            self.camera.wait(3000)
            event.accept()
            return
        event.ignore()
        self._owners.clear()
        self._camera_stop_timer.stop()
        if self.camera.isRunning():
            self.camera.stop()
            self.camera.wait(2000)
        self.hide()
        self._tray.showMessage("Glance", "Still protecting this PC in the background.",
                               QSystemTrayIcon.Information, 2500)

    def quit(self) -> None:
        self._quitting = True
        self._camera_stop_timer.stop()
        self.camera.stop()
        self.camera.wait(3000)
        QApplication.quit()


def build_tray(window: MainWindow) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(app_icon(), window)
    menu = QMenu()

    open_action = QAction("Open Glance", menu)
    open_action.triggered.connect(lambda: (window.showNormal(), window.raise_(),
                                           window.activateWindow()))
    menu.addAction(open_action)

    lock_action = QAction("Lock now", menu)
    lock_action.triggered.connect(backend.lock_workstation)
    menu.addAction(lock_action)

    pause_action = QAction("Pause protection", menu, checkable=True)
    pause_action.setChecked(not backend.agent_running())

    def toggle_pause(checked: bool) -> None:
        ok, message = (backend.stop_agent() if checked else backend.start_agent())
        if not ok:
            tray.showMessage("Glance", message, QSystemTrayIcon.Warning, 4000)
        window._refresh_chip()

    pause_action.toggled.connect(toggle_pause)
    menu.addAction(pause_action)

    logs_action = QAction("Open logs folder", menu)
    logs_action.triggered.connect(lambda: os.startfile(str(backend.LOG_DIR)))
    menu.addAction(logs_action)

    menu.addSeparator()
    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(window.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)

    def activated(reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            window.showNormal()
            window.raise_()
            window.activateWindow()
            window._navigate(0)

    tray.activated.connect(activated)
    window.attach_tray(tray)
    tray.show()
    return tray


def claim_instance() -> bool:
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    if not handle:
        return True
    return ctypes.get_last_error() != 183


def main() -> int:
    if not claim_instance():
        # Bring the existing window forward instead of starting a second agent viewer.
        ctypes.windll.user32.MessageBoxW(
            None, "Glance is already running. Look for it in the notification area.",
            "Glance", 0x40,
        )
        return 0

    app = QApplication(sys.argv)
    app.setOrganizationName("Glance")
    app.setApplicationName("Glance")
    app.setApplicationDisplayName("Glance")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(resolve_theme("dark").stylesheet)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Glance.Desktop")
    except (AttributeError, OSError):
        pass  # Older shells still show the executable's embedded icon.

    window = MainWindow()
    build_tray(window)
    window.show()
    QTimer.singleShot(250, window.startup_sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
