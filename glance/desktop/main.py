"""Glance desktop window: sidebar, pages, tray."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
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
import time

from .. import __version__
from . import backend
from . import system_check
from .camera import CameraWorker
from .face_gate import FaceGateDialog
from .models_setup import ModelsDialog
from .pages import (
    AboutPage, CameraPage, Dashboard, EnrollPage, PasswordDialog,
    PasswordPage, WindowsPasswordFallbackDialog, RecognitionPage, Task,
)
from .. import protection_state
from .recovery import should_recover_agent
from .settings_widgets import FloatingTabBar
from .navigation import FloatingNavigation, WindowControls, style_native_window
from .theme import (
    ACCENT,
    app_icon,
    backdrop_pixmap,
    resolve_theme,
    windows_appearance,
    Dot,
)
from .splash import StartupSplash
from .motion import motion_enabled
from . import ui_state

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
        self._compact_size = (600, 800)
        self._enrollment_size = self._compact_size
        self.resize(*self._compact_size)
        self.setMinimumSize(520, 650)
        self._backdrop_cache = None
        self._backdrop_key = None

        self.settings = backend.Settings.load()
        self._current_index = 0
        self._motion_enabled = motion_enabled()
        self._page_animation: QPropertyAnimation | None = None
        self._page_effects: dict[int, QGraphicsOpacityEffect] = {}
        self._owners: set[str] = set()
        self._quitting = False
        self._agent_was_active_this_session = backend.agent_running()
        self._recovery_task: Task | None = None
        self._last_recovery_attempt = 0.0
        self._camera_stop_timer = QTimer(self)
        self._camera_stop_timer.setSingleShot(True)
        self._camera_stop_timer.setInterval(250)
        self._camera_stop_timer.timeout.connect(self._stop_camera_if_unowned)

        self.camera = CameraWorker(
            self.settings.camera_index_for_display(), self.settings.threshold, self
        )
        self.camera.failed.connect(self._camera_failed)

        central = QWidget()
        central.setObjectName("windowRoot")
        self._root_layout = QVBoxLayout(central)
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
        self.nav = FloatingNavigation(
            ("General", "Your Face", "Password", "Camera", "Recognition", "About"),
            self,
        )
        self.nav.indexChanged.connect(self._navigate)

        header = QFrame(central)
        header.setObjectName("titleBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 12, 8)
        header_layout.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        logo.setFixedSize(24, 24)
        header_layout.addWidget(logo)
        title = QLabel("Glance")
        title.setObjectName("brand")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.status_pill = QFrame(header)
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setAttribute(Qt.WA_StyledBackground, True)
        status_layout = QHBoxLayout(self.status_pill)
        status_layout.setContentsMargins(9, 5, 10, 5)
        status_layout.setSpacing(7)
        self.chip_dot = Dot()
        self.chip_label = QLabel("Checking…")
        self.chip_label.setObjectName("statusText")
        status_layout.addWidget(self.chip_dot)
        status_layout.addWidget(self.chip_label)
        self.nav.status_dot = self.chip_dot
        self.nav.status_label = self.chip_label
        header_layout.addWidget(self.status_pill)

        self.window_controls = WindowControls(header)
        self.window_controls.closeRequested.connect(self.close)
        self.window_controls.minimizeRequested.connect(self.showMinimized)
        self.window_controls.zoomRequested.connect(self._toggle_zoom)
        header_layout.addWidget(self.window_controls)

        self._root_layout.addWidget(header)
        self._root_layout.addWidget(self.stack, 1)
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(12, 7, 12, 14)
        nav_row.addStretch(1)
        nav_row.addWidget(self.nav)
        nav_row.addStretch(1)
        self._root_layout.addLayout(nav_row)
        self.setCentralWidget(central)

        self._native_acrylic = style_native_window(self)
        self._apply_frame_mode()

        self.camera.ready.connect(self.enroll.on_camera_ready)
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
        # Controls are managed by the compact title-bar layout.
        controls.set_maximised(self.isMaximized() or self.isFullScreen())

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
        if self._native_acrylic:
            painter.fillPath(path, QColor(10, 22, 31, 54))
        else:
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
        return FaceGateDialog(
            self, purpose,
            password_fallback=self._request_windows_password_fallback,
        ).exec() == QDialog.Accepted

    def _request_windows_password_fallback(self) -> bool:
        return WindowsPasswordFallbackDialog(self).exec() == QDialog.Accepted

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
        previous_index = self._current_index
        self._current_index = index
        self.stack.setCurrentIndex(index)
        self.nav.set_current_index(index)
        self.window_controls.raise_()
        self._transition_page(previous_index, index)

    def _transition_page(self, previous_index: int, index: int) -> None:
        """Fade non-camera page content once; camera previews switch immediately."""
        if self._page_animation is not None:
            self._page_animation.stop()
            self._page_animation.deleteLater()
            self._page_animation = None
        for effect in self._page_effects.values():
            effect.setOpacity(1.0)
        if (not self._motion_enabled or index == 3 or previous_index == 3
                or previous_index == index):
            return
        page = self.stack.widget(index)
        effect = self._page_effects.get(index)
        if effect is None:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            self._page_effects[index] = effect
        effect.setOpacity(0.82)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(160)
        animation.setStartValue(0.82)
        animation.setEndValue(1.0)
        animation.finished.connect(lambda: self._page_transition_finished(animation))
        self._page_animation = animation
        animation.start()

    def _page_transition_finished(self, animation: QPropertyAnimation) -> None:
        if self._page_animation is animation:
            self._page_animation = None
        animation.deleteLater()

    def show_whats_new_if_needed(self) -> None:
        if not ui_state.should_show_whats_new(__version__):
            return
        QMessageBox.information(
            self,
            f"What's new in Glance {__version__}",
            "Windows unlock and in-app face checks now look for natural facial "
            "movement. This motion check is imperfect and a moving replay may pass. "
            "Your Windows password remains available as a fallback for in-app face checks.",
        )
        ui_state.mark_whats_new_shown(__version__)

    def offer_startup_if_needed(self) -> None:
        state = backend.task_state(0)
        if (not state.get("present") or state.get("enabled") or
                not ui_state.should_offer_startup_prompt()):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Start Glance with Windows?")
        box.setText(
            "Glance can monitor for face unlock quietly in the background after you "
            "sign in. The Glance window will stay closed. You can change this later "
            "in Settings."
        )
        enable = box.addButton("Enable", QMessageBox.AcceptRole)
        later = box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        choice = box.clickedButton()
        if choice is later:
            ui_state.dismiss_startup_prompt()
        elif choice is enable:
            ok, message = backend.set_autostart(True)
            if not ok:
                QMessageBox.warning(self, "Glance", message)

    def startup_sequence(self) -> None:
        """Offer the model flow, then continue the normal startup checks."""
        ModelsDialog.ensure_models(self)
        self.show_whats_new_if_needed()
        self.offer_startup_if_needed()

    # -- reactions to changes ---------------------------------------------
    def on_identities_changed(self, name: str) -> None:
        answer = QMessageBox.question(
            self, "Glance",
            f"Samples for {name} are saved.\n\nRestart protection so the agent reloads the "
            "enrolled faces now?",
        )
        if answer == QMessageBox.Yes:
            self.restart_agent()

    def _apply_display_preference(self) -> None:
        _apply_display_choice(self, self.settings)

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
        if ok:
            self._agent_was_active_this_session = True
        if not ok:
            QMessageBox.warning(self, "Glance", message)
        self._refresh_chip()

    # -- tray -------------------------------------------------------------
    def attach_tray(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray

    def _refresh_chip(self) -> None:
        running = backend.agent_running()
        paused = protection_state.is_paused_until_wake()
        if running:
            self._agent_was_active_this_session = True
        self._maybe_recover_agent(running)
        status = "Protected" if running else (
            "Paused until wake" if paused else "Protection stopped"
        )
        self.nav.set_status(
            status,
            ACCENT if running else "#e3b341",
        )
        if self._tray is not None:
            self._tray.setToolTip(
                "Glance · protected" if running else f"Glance · {status.lower()}"
            )

    def _maybe_recover_agent(self, running: bool) -> None:
        paused = protection_state.is_paused_until_wake()
        installed = backend.task_state().get("present", False)
        if not should_recover_agent(
            self._agent_was_active_this_session, installed, running, paused
        ):
            return
        now = time.monotonic()
        if self._recovery_task is not None or now - self._last_recovery_attempt < 30:
            return
        self._last_recovery_attempt = now
        self._recovery_task = Task(backend.resume_agent, self)
        self._recovery_task.done.connect(self._agent_recovery_finished)
        self._recovery_task.start()

    def _agent_recovery_finished(self, ok: bool, message: str) -> None:
        self._recovery_task = None
        if ok:
            self._agent_was_active_this_session = True
        elif self._tray is not None:
            self._tray.showMessage(
                "Glance protection stopped",
                f"Glance could not restart face protection. Open Glance and select Resume protection. {message}",
                QSystemTrayIcon.Warning,
                6000,
            )
        self._refresh_chip()

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
    pause_action.setToolTip("Protection resumes when this PC wakes from sleep.")
    pause_action.setChecked(not backend.agent_running())

    def toggle_pause(checked: bool) -> None:
        ok, message = (
            backend.pause_agent_until_wake() if checked else backend.resume_agent()
        )
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


_kernel32_checked = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32_checked.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32_checked.CreateMutexW.restype = wintypes.HANDLE

# Kept alive for the life of the process so the mutex stays held; closing or
# dropping this handle would let a second instance in.
_instance_mutex = None


def _acquire_mutex(name: str) -> bool:
    """Return True when this process is the sole holder of ``name``.

    use_last_error=True makes ctypes snapshot the error code immediately after
    the call, so ERROR_ALREADY_EXISTS can be trusted here. Without it the code
    always reads stale error 0 and a second instance is let in. Zeroing the
    slot first matters too: Win32 does not clear GetLastError on success, so
    without this a fresh creation could read a stale error from an unrelated
    earlier call.
    """
    global _instance_mutex
    ctypes.set_last_error(0)
    handle = _kernel32_checked.CreateMutexW(None, False, name)
    if not handle:
        return True  # cannot tell - being able to open the app matters more than tidiness
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        _kernel32_checked.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def claim_instance() -> bool:
    return _acquire_mutex(APP_MUTEX)


def create_and_show_main_window(app: QApplication) -> MainWindow:
    """Show the bounded splash while the native window is constructed."""
    splash = StartupSplash()
    splash.show()
    app.processEvents()
    try:
        window = MainWindow()
        build_tray(window)
        window.show()
    except BaseException:
        splash.close()
        raise
    splash.finish(window)
    return window


def ensure_compatible_pc() -> bool:
    """Show a clear blocking message only for unsupported OS/architecture."""
    result = system_check.check_current_system()
    if result.supported:
        return True
    detail = "\n".join(result.blockers)
    QMessageBox.critical(
        None,
        "Glance can’t run on this PC",
        f"This app can’t run on this PC.\n\n{detail}",
    )
    return False


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
    if not ensure_compatible_pc():
        return 1
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Glance.Desktop")
    except (AttributeError, OSError):
        pass  # Older shells still show the executable's embedded icon.

    window = create_and_show_main_window(app)
    _apply_display_choice(window, window.settings)
    QTimer.singleShot(250, window.startup_sequence)
    return app.exec()


def _apply_display_choice(window, settings) -> None:
    """Best-effort placement of the app window on the chosen display.

    "Main display" is the primary screen; "external" prefers the last
    non-primary screen when one exists, and falls back to the primary.
    """
    try:
        screens = QApplication.screens()
        if not screens:
            return
        if settings.display_choice == "external" and len(screens) > 1:
            target = screens[-1]
        else:
            target = screens[0]
        window.windowHandle().setScreen(target)
    except Exception:  # noqa: BLE001 - placement is cosmetic; never fail startup
        pass


if __name__ == "__main__":
    raise SystemExit(main())
