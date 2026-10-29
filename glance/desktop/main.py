"""Glance desktop window: sidebar, pages, tray."""

from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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
from .face_gate import FaceGateDialog
from .models_setup import ModelsDialog
from .onboarding import FirstRunDialog
from .pages import (
    AboutPage, CameraPage, Dashboard, EnrollPage, PasswordDialog,
    PasswordPage, RecognitionPage,
)
from .settings_widgets import FloatingTabBar
from .navigation import Sidebar, WindowControls, style_native_window
from .theme import (
    ACCENT,
    app_icon,
    backdrop_pixmap,
    resolve_theme,
    windows_appearance,
)

APP_MUTEX = "Local\\GlanceDesktop"


class MainWindow(QMainWindow):
    # The glass fills the window itself; the only transparent pixels left are the
    # four rounded corners, which are the window's shape rather than a gutter.
    RADIUS = 20

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glance")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._compact_size = (1060, 780)
        self._enrollment_size = self._compact_size
        self.resize(*self._compact_size)
        self.setMinimumSize(980, 720)
        self._backdrop_cache = None
        self._backdrop_key = None

        self.settings = backend.Settings.load()
        self._current_index = 0
        self._owners: set[str] = set()
        self._quitting = False
        self._camera_stop_timer = QTimer(self)
        self._camera_stop_timer.setSingleShot(True)
        self._camera_stop_timer.setInterval(250)
        self._camera_stop_timer.timeout.connect(self._stop_camera_if_unowned)

        self.camera = CameraWorker(self.settings.camera, self.settings.threshold, self)
        self.camera.failed.connect(self._camera_failed)

        central = QWidget()
        central.setObjectName("windowRoot")
        self._root_layout = QHBoxLayout(central)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

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
            self.stack.addWidget(page)
        self.nav = Sidebar(
            (
                ("", ("General",)),
                ("Authentication", ("Your Face", "Password", "Camera", "Recognition")),
                ("Glance", ("About",)),
            ),
            self,
        )
        self.nav.indexChanged.connect(self._navigate)
        self._root_layout.addWidget(self.nav)
        self._root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.window_controls = WindowControls(central)
        self.window_controls.closeRequested.connect(self.close)
        self.window_controls.minimizeRequested.connect(self.showMinimized)
        self.window_controls.zoomRequested.connect(self._toggle_zoom)
        self.window_controls.raise_()

        style_native_window(self)
        self._apply_frame_mode()

        self.chip_dot = self.nav.status_dot
        self.chip_label = self.nav.status_label
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

    # -- window chrome ----------------------------------------------------
    def _card_rect(self) -> QRect:
        return self.rect()

    def _apply_frame_mode(self) -> None:
        framed = not (self.isMaximized() or self.isFullScreen())
        self.nav.setProperty("framed", framed)
        self.nav.style().unpolish(self.nav)
        self.nav.style().polish(self.nav)
        self.window_controls.set_maximised(not framed)
        self._place_controls()
        self._backdrop_key = None
        self.update()

    def _place_controls(self) -> None:
        controls = getattr(self, "window_controls", None)
        if controls is None:
            return
        framed = not (self.isMaximized() or self.isFullScreen())
        x = self.width() - (12 if framed else 8) - controls.width()
        y = 6 if framed else 2
        controls.move(max(0, x), max(0, y))

    def _toggle_zoom(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "window_controls"):
            self._apply_frame_mode()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_controls()
        self._backdrop_key = None

    def _backdrop(self, size):
        ratio = round(self.devicePixelRatioF(), 2) or 1.0
        key = (int(size.width()), int(size.height()), ratio)
        if self._backdrop_key != key or self._backdrop_cache is None:
            self._backdrop_cache = backdrop_pixmap(size, ratio)
            self._backdrop_key = key
        return self._backdrop_cache

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        framed = not (self.isMaximized() or self.isFullScreen())
        rect = QRectF(self.rect())
        radius = float(self.RADIUS) if framed else 0.0

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        painter.drawPixmap(rect.topLeft(), self._backdrop(rect.size()))
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    def _edges_at(self, pos) -> Qt.Edge:
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        card = self._card_rect()
        near = 8
        edges = Qt.Edge(0)
        if card.left() - near <= pos.x() <= card.left() + near:
            edges |= Qt.LeftEdge
        elif card.right() - near <= pos.x() <= card.right() + near:
            edges |= Qt.RightEdge
        if card.top() - near <= pos.y() <= card.top() + near:
            edges |= Qt.TopEdge
        elif card.bottom() - near <= pos.y() <= card.bottom() + near:
            edges |= Qt.BottomEdge
        return edges

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            handle = self.windowHandle()
            edges = self._edges_at(event.position().toPoint())
            if handle is not None and edges:
                handle.startSystemResize(edges)
                return
            top = self._card_rect().top()
            if handle is not None and event.position().y() <= top + 46:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        edges = self._edges_at(event.position().toPoint())
        if edges & Qt.LeftEdge and edges & Qt.TopEdge:
            cursor = Qt.SizeFDiagCursor
        elif edges & Qt.RightEdge and edges & Qt.BottomEdge:
            cursor = Qt.SizeFDiagCursor
        elif edges & Qt.RightEdge and edges & Qt.TopEdge:
            cursor = Qt.SizeBDiagCursor
        elif edges & Qt.LeftEdge and edges & Qt.BottomEdge:
            cursor = Qt.SizeBDiagCursor
        elif edges & (Qt.LeftEdge | Qt.RightEdge):
            cursor = Qt.SizeHorCursor
        elif edges & (Qt.TopEdge | Qt.BottomEdge):
            cursor = Qt.SizeVerCursor
        else:
            cursor = Qt.ArrowCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and event.position().y() <= self._card_rect().top() + 46
        ):
            self._toggle_zoom()
            return
        super().mouseDoubleClickEvent(event)

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
    GATED_PAGES = {1: "Your Face", 2: "Password"}

    def request_face_gate(self, purpose: str) -> bool:
        """Ask for an enrolled face before a sensitive page opens.

        With no enrolled faces the gate is skipped so first-run setup can work;
        every later visit is checked against any enrolled person.
        """
        if not backend.identity_summary(0):
            return True
        return FaceGateDialog(self, purpose).exec() == QDialog.Accepted

    def _windows_password_stored(self) -> bool:
        try:
            return bool(backend.credential_status(0).get("present"))
        except Exception:  # noqa: BLE001 - an unreadable credential fails closed
            return False

    def _navigate(self, index: int) -> None:
        if index == 1 and not self._windows_password_stored():
            self.nav.set_current_index(self._current_index)
            QMessageBox.information(
                self, "Glance",
                "Set your Windows password before enrolling a face. Glance replays "
                "your verified password after Windows matches your face.",
            )
            self._navigate(2)
            return
        purpose = self.GATED_PAGES.get(index)
        if purpose is not None and not self.request_face_gate(purpose):
            self.nav.set_current_index(self._current_index)
            return
        self._current_index = index
        self.stack.setCurrentIndex(index)
        self.nav.set_current_index(index)
        self.window_controls.raise_()

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
        self.nav.set_status(
            "Protected" if running else "Paused",
            ACCENT if running else "#e3b341",
        )
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
