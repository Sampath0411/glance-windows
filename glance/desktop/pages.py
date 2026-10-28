"""The four screens: overview, enrolment, settings, logs."""

from __future__ import annotations

import json
import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import IdentityStore

from . import backend
from .models_setup import ModelsDialog
from .navigation import navigation_icon
from .theme import ACCENT, DANGER, MUTED, WARN, Card, Dot, Preview
from .settings_widgets import (
    CheckBox,
    NumberField,
    SegmentedTiles,
    Select,
    SettingsDivider,
    SettingsGroup,
    SettingsRow,
)


def _title(text: str, size: str = "h1") -> QLabel:
    label = QLabel(text)
    label.setObjectName(size)
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def _page_header(title: str, subtitle: str, icon_index: int) -> QWidget:
    """Icon, page title and one-line description, like the reference layout."""
    host = QWidget()
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 2)
    grid.setHorizontalSpacing(11)
    grid.setVerticalSpacing(1)

    glyph = QLabel()
    bitmap = navigation_icon(icon_index, "#e9eff8").pixmap(40, 40)
    bitmap.setDevicePixelRatio(2)
    glyph.setPixmap(bitmap)
    glyph.setFixedSize(20, 20)
    grid.addWidget(glyph, 0, 0, 2, 1, Qt.AlignTop)

    heading = _title(title)
    heading.setObjectName("h1")
    grid.addWidget(heading, 0, 1)
    caption = QLabel(subtitle)
    caption.setObjectName("pageSub")
    caption.setWordWrap(True)
    grid.addWidget(caption, 1, 1)
    grid.setColumnStretch(1, 1)
    return host


def _scrollable(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


class Task(QThread):
    """Runs one blocking call (usually a UAC-elevated helper) off the UI thread."""

    done = Signal(bool, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self._function = function

    def run(self) -> None:
        try:
            ok, message = self._function()
        except Exception as error:  # noqa: BLE001 - show the failure, never crash the UI
            ok, message = False, str(error)
        self.done.emit(ok, message)


class DashboardSnapshotTask(QThread):
    """Read subprocess-backed dashboard state without blocking Qt's UI thread."""

    done = Signal(object)

    def run(self) -> None:
        try:
            snapshot = {
                "task": backend.task_state(),
                "running": backend.agent_running(),
                "credential": backend.credential_status(),
                "identities": backend.identity_summary(),
                "unlock": backend.last_unlock(),
            }
        except Exception as error:  # noqa: BLE001
            snapshot = {"error": str(error)}
        self.done.emit(snapshot)


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
class Dashboard(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._paused_by_user = False
        self._agent_installed = False

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 118, 0)
        header.addWidget(
            _page_header("General", "Face unlock for this Windows account.", 0), 1
        )
        header.addStretch(0)
        self.lock_button = QPushButton("Lock now")
        self.lock_button.clicked.connect(backend.lock_workstation)
        header.addWidget(self.lock_button, 0, Qt.AlignTop)
        layout.addLayout(header)

        # status
        status = Card()
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(20, 18, 20, 18)
        status_layout.setSpacing(14)

        self.status_dot = Dot(MUTED)
        status_layout.addWidget(self.status_dot, 0, Qt.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        self.status_title = _title("Checking…", "big")
        self.status_detail = _muted("")
        text_column.addWidget(self.status_title)
        text_column.addWidget(self.status_detail)
        status_layout.addLayout(text_column, 1)

        self.pause_button = QPushButton("Pause protection")
        self.pause_button.clicked.connect(self.toggle_pause)
        status_layout.addWidget(self.pause_button, 0, Qt.AlignTop)
        layout.addWidget(status)

        self.autostart = CheckBox("Run protection when I sign in")
        self.autostart.toggled.connect(self._set_autostart)
        self._autostart_task: Task | None = None

        # setup facts
        layout.addWidget(_title("Your setup", "h2"))

        self.identity_card = self._fact("Identity", "—", "")
        self.credential_card = self._fact("Windows credential", "—", "")
        self.unlock_card = self._fact("Last face unlock", "—", "")
        set_password = QPushButton("Set password…")
        set_password.clicked.connect(self._set_password)
        self.credential_card[3].addWidget(set_password)
        setup = SettingsGroup()
        setup.add_row(self.identity_card[0])
        setup.add_row(self.credential_card[0])
        setup.add_row(self.unlock_card[0])
        layout.addWidget(setup)
        layout.addWidget(_title("Preferences", "h2"))
        preferences = SettingsGroup()
        preferences.add_row(SettingsRow("Start when I sign in", "Run protection automatically after Windows sign-in.", self.autostart))
        self.autostart.setText("")
        self.autostart.setAccessibleName("Run protection when I sign in")
        layout.addWidget(preferences)
        layout.addWidget(_muted("Your Windows PIN and password are still available."))

        # camera
        camera_card = Card()
        camera_layout = QVBoxLayout(camera_card)
        camera_layout.setContentsMargins(18, 16, 18, 16)
        camera_layout.setSpacing(10)
        camera_header = QHBoxLayout()
        camera_header.addWidget(_title("Camera", "h2"))
        camera_header.addStretch(1)
        self.camera_toggle = CheckBox("Live preview")
        self.camera_toggle.setChecked(False)
        self.camera_toggle.toggled.connect(self._toggle_camera)
        camera_header.addWidget(self.camera_toggle)
        camera_layout.addLayout(camera_header)
        self.preview = Preview()
        self.preview.setMinimumHeight(260)
        camera_layout.addWidget(self.preview)
        camera_card.setParent(self)
        camera_card.hide()
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(inner))

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._refresh_task: DashboardSnapshotTask | None = None
        self._pause_task: Task | None = None

    def _fact(self, title: str, value: str, detail: str):
        row = SettingsRow(title, detail)
        row.subtitle.setVisible(True)
        value_label = QLabel(value)
        value_label.setObjectName("rowTitle")
        row.trailing_layout.insertWidget(0, value_label)
        return row, value_label, row.subtitle, row.trailing_layout

    # -- lifecycle --------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()
        self._timer.start()
        if self.camera_toggle.isChecked():
            self.window.acquire_camera("dashboard")

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()
        self.window.release_camera("dashboard")

    def _toggle_camera(self, enabled: bool) -> None:
        if enabled:
            self.window.acquire_camera("dashboard")
        else:
            self.window.release_camera("dashboard")
            self.preview.clear()

    def on_preview(self, image, info) -> None:
        if not self.isVisible():
            return
        self.preview.set_frame(image, info.faces, info.label, info.hint)

    # -- state ------------------------------------------------------------
    def refresh(self) -> None:
        if self._refresh_task is not None and self._refresh_task.isRunning():
            return
        self._refresh_task = DashboardSnapshotTask(QApplication.instance())
        self._refresh_task.done.connect(self._apply_snapshot)
        self._refresh_task.start()

    def _apply_snapshot(self, snapshot: dict) -> None:
        self._refresh_task = None
        if "error" in snapshot:
            self.status_detail.setText(f"Could not refresh status: {snapshot['error']}")
            return
        settings = self.window.settings
        state = snapshot["task"]
        running = snapshot["running"]
        self._agent_installed = bool(state.get("present"))
        self.autostart.blockSignals(True)
        self.autostart.setChecked(bool(state.get("enabled")))
        self.autostart.blockSignals(False)
        self.autostart.setEnabled(bool(state.get("present")))

        if not state.get("present"):
            self.status_dot.set_color(DANGER)
            self.status_title.setText("Windows unlock not enabled")
            self.status_detail.setText("Open Password to verify your Windows password and enable face unlock.")
            self.pause_button.setText("Set up Windows unlock")
            self.pause_button.setEnabled(True)
        elif running:
            self.status_dot.set_color(ACCENT)
            self.status_title.setText("Protection on")
            who = settings.identity or "no identity selected"
            self.status_detail.setText(
                f"Waiting for a lock · {who} · threshold {settings.threshold:.2f}"
            )
            self.pause_button.setText("Pause protection")
            self.pause_button.setEnabled(True)
        else:
            self.status_dot.set_color(WARN)
            self.status_title.setText("Paused")
            self.status_detail.setText("Locks will need your PIN or password.")
            self.pause_button.setText("Resume protection")
            self.pause_button.setEnabled(True)

        credential = snapshot["credential"]
        if credential["present"]:
            account = credential["account"] or ""
            short = account.split("\\")[-1] if account else "account unknown"
            self.credential_card[1].setText("Stored")
            self.credential_card[2].setText(short)
            self.credential_card[2].setToolTip(account)
        else:
            self.credential_card[1].setText("Not set")
            self.credential_card[2].setText("Face unlock cannot sign you in yet")
            self.credential_card[2].setToolTip("")

        identities = snapshot["identities"]
        chosen = settings.identity or ""
        samples = next((i["samples"] for i in identities if i["name"] == chosen), 0)
        if identities:
            self.identity_card[1].setText(chosen or f"{len(identities)} enrolled")
            self.identity_card[2].setText(f"{samples} samples" if chosen else "Select one in Recognition")
        else:
            self.identity_card[1].setText("None")
            self.identity_card[2].setText("Enrol a face to begin")

        self.unlock_card[1].setText("—")
        line = snapshot["unlock"]
        if line != "never":
            stamp = " ".join(line.split()[:2])
            self.unlock_card[1].setText(stamp[11:] if len(stamp) > 11 else stamp)
            name = line.split("granted", 1)[1].strip().split(" ")[0]
            self.unlock_card[2].setText(f"{name} · most recent grant")
        else:
            self.unlock_card[2].setText("no unlock recorded")

    def toggle_pause(self) -> None:
        if not self._agent_installed:
            self.window._navigate(2)
            return
        if self._pause_task is not None:
            return
        self.pause_button.setEnabled(False)
        operation = backend.stop_agent if backend.agent_running() else backend.start_agent
        self._pause_task = Task(operation, self)
        self._pause_task.done.connect(self._pause_finished)
        self._pause_task.start()

    def _pause_finished(self, ok: bool, message: str) -> None:
        self._pause_task = None
        if not ok:
            QMessageBox.warning(self, "Glance", message)
        self.refresh()

    def _set_autostart(self, enabled: bool) -> None:
        if self._autostart_task is not None:
            return
        self.autostart.setEnabled(False)
        self._autostart_task = Task(lambda: backend.set_autostart(enabled), self)
        self._autostart_task.done.connect(self._autostart_finished)
        self._autostart_task.start()

    def _autostart_finished(self, ok: bool, message: str) -> None:
        self._autostart_task = None
        self.autostart.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "Glance", message)
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not self.autostart.isChecked())
            self.autostart.blockSignals(False)

    def _set_password(self) -> None:
        account = backend.credential_status()["account"] or backend.ACCOUNT_HINT
        dialog = PasswordDialog(self, account)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()


# --------------------------------------------------------------------------- #
# enrolment
# --------------------------------------------------------------------------- #
class EnrollPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._samples: list[np.ndarray] = []
        self._camera_ready = False
        self._capture_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)
        layout.addWidget(_page_header(
            "Your Face",
            "Look at the camera in even light and capture eight or more samples, "
            "turning your head slightly between them.",
            1,
        ))

        body = QHBoxLayout()
        body.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(12)
        self.preview = Preview()
        self.preview.setMinimumWidth(280)
        left.addWidget(self.preview, 1)

        camera_row = QHBoxLayout()
        self.camera_status = _muted("Camera is starting…")
        camera_row.addWidget(self.camera_status, 1)
        self.retry_camera_button = QPushButton("Retry camera")
        self.retry_camera_button.clicked.connect(self._retry_camera)
        self.retry_camera_button.hide()
        camera_row.addWidget(self.retry_camera_button)
        left.addLayout(camera_row)

        row = QHBoxLayout()
        self.capture_button = QPushButton("Capture sample")
        self.capture_button.setObjectName("primary")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self._capture)
        row.addWidget(self.capture_button)
        self.counter = _muted("0 samples")
        row.addWidget(self.counter)
        row.addStretch(1)
        left.addLayout(row)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(12)

        form = Card()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(18, 16, 18, 16)
        form_layout.setSpacing(8)
        form_layout.addWidget(_title("Name", "h2"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Your name")
        form_layout.addWidget(self.name_edit)
        form_layout.addWidget(_muted("Capturing into an existing name adds samples to that "
                                     "person instead of creating a second identity."))
        self.quality = _muted("No samples yet")
        self.quality.setWordWrap(True)
        form_layout.addWidget(self.quality)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save samples")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.clear_button)
        form_layout.addLayout(buttons)
        right.addWidget(form)

        people = Card()
        people_layout = QVBoxLayout(people)
        people_layout.setContentsMargins(18, 16, 18, 16)
        people_layout.setSpacing(8)
        people_layout.addWidget(_title("Enrolled", "h2"))
        self.people_box = QVBoxLayout()
        self.people_box.setSpacing(6)
        people_layout.addLayout(self.people_box)
        people_layout.addStretch(1)
        right.addWidget(people, 1)

        body.addLayout(right, 2)
        layout.addLayout(body, 1)

    # -- lifecycle --------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.window.acquire_camera("enroll")
        self.refresh_people()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.window.release_camera("enroll")

    def on_preview(self, image, info) -> None:
        if not self.isVisible():
            return
        self.preview.set_frame(image, info.faces, info.label, info.hint)

    def on_camera_ready(self) -> None:
        self._camera_ready = True
        self.camera_status.setText("Camera ready · look at the lens")
        self.camera_status.setStyleSheet(f"color: {ACCENT};")
        self.retry_camera_button.hide()
        self._refresh_capture_button()

    def on_camera_failed(self, message: str) -> None:
        self._camera_ready = False
        self._capture_pending = False
        if not backend.models_ready():
            message = "Face models are not installed yet."
            self.retry_camera_button.setText("Download models")
        else:
            self.retry_camera_button.setText("Retry camera")
        self.camera_status.setText(message)
        self.camera_status.setStyleSheet(f"color: {DANGER};")
        self.retry_camera_button.show()
        self._refresh_capture_button()

    def _refresh_capture_button(self) -> None:
        self.capture_button.setEnabled(self._camera_ready and not self._capture_pending)

    def on_capture(self, capture) -> None:
        self._capture_pending = False
        self._refresh_capture_button()
        if capture.embedding is None or not capture.ok:
            self.quality.setText(capture.message or "Capture failed")
            self.quality.setStyleSheet(f"color: {WARN};")
            return

        self._samples.append(capture.embedding)
        self.counter.setText(f"{len(self._samples)} samples")
        note = capture.message
        if capture.match_name:
            note += f" · looks like {capture.match_name} ({capture.match_score:.2f})"
        self.quality.setText(note)
        self.quality.setStyleSheet(f"color: {ACCENT};")

    # -- actions ----------------------------------------------------------
    def _capture(self) -> None:
        if not self._camera_ready or self._capture_pending:
            return
        if self.window.camera.request_capture():
            self._capture_pending = True
            self._refresh_capture_button()
            self.quality.setText("Capturing sample…")
            self.quality.setStyleSheet("")

    def _retry_camera(self) -> None:
        if not backend.models_ready():
            self.camera_status.setText("Face models are not installed yet.")
            self.camera_status.setStyleSheet(f"color: {WARN};")
            ModelsDialog(self).exec()
            if not backend.models_ready():
                self.retry_camera_button.setText("Download models")
                return
        self.retry_camera_button.setText("Retry camera")
        self._camera_ready = False
        self._capture_pending = False
        self.camera_status.setText("Restarting camera…")
        self.camera_status.setStyleSheet("")
        self.retry_camera_button.hide()
        self._refresh_capture_button()
        self.window.retry_camera("enroll")

    def _clear(self) -> None:
        self._samples.clear()
        self.counter.setText("0 samples")
        self.quality.setText("No samples yet")
        self.quality.setStyleSheet("")

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Glance", "Give this face a name first.")
            return
        if len(self._samples) < 3:
            QMessageBox.information(self, "Glance", "Capture at least three samples.")
            return

        try:
            store = IdentityStore(backend.IDENTITIES_PATH)
            for embedding in self._samples:
                store.add_sample(name, embedding)
            store.save()
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Glance", f"Could not save: {error}")
            return

        count = len(self._samples)
        self._samples.clear()
        self.counter.setText("0 samples")
        self.quality.setText(f"Saved {count} samples for {name}")
        self.quality.setStyleSheet(f"color: {ACCENT};")
        self.window.camera.reload_store()
        self.refresh_people()
        self.window.on_identities_changed(name)

    def refresh_people(self) -> None:
        while self.people_box.count():
            item = self.people_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        identities = backend.identity_summary()
        if not identities:
            self.people_box.addWidget(_muted("Nobody enrolled yet."))
            return

        for person in identities:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(f"{person['name']}")
            detail = QLabel(f"{person['samples']} samples")
            detail.setObjectName("muted")
            layout.addWidget(label)
            layout.addWidget(detail)
            layout.addStretch(1)

            remove = QPushButton("Remove")
            remove.setObjectName("danger")
            remove.clicked.connect(lambda _=False, n=person["name"]: self._remove(n))
            layout.addWidget(remove)
            self.people_box.addWidget(row)

    def _remove(self, name: str) -> None:
        confirm = QMessageBox.question(
            self, "Glance", f"Remove {name}? This deletes their face samples."
        )
        if confirm != QMessageBox.Yes:
            return
        ok, message = backend.delete_identity(name)
        if not ok:
            QMessageBox.warning(self, "Glance", message)
            return
        selected_identity_removed = self.window.settings.identity == name
        if selected_identity_removed:
            self.window.settings = backend.Settings.load()
        self.window.camera.reload_store()
        self.refresh_people()
        if selected_identity_removed:
            self.quality.setText(message)
            self.quality.setStyleSheet(f"color: {WARN};")
        else:
            self.window.on_identities_changed(name)


# --------------------------------------------------------------------------- #
# recognition
# --------------------------------------------------------------------------- #
class SettingsPage(QWidget):
    PRESETS = {"loose": 50, "balanced": 60, "strict": 72}

    def __init__(self, window):
        super().__init__()
        self.window = window

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(16)

        layout.addWidget(_page_header(
            "Recognition",
            "Decide how confidently Glance must recognize you.",
            4,
        ))

        card = Card()
        form = QVBoxLayout(card)
        form.setContentsMargins(18, 14, 18, 16)
        form.setSpacing(10)

        form.addWidget(_title("Who may unlock", "h2"))
        self.identity = Select()
        self.identity.currentIndexChanged.connect(self._selection_changed)
        form.addWidget(self.identity)
        form.addWidget(_muted("Only the selected person can unlock this account. Choose an enrolled face to enable recognition."))

        form.addWidget(_title("Matching profile", "h2"))
        self.presets = SegmentedTiles((
            ("loose", "Loose", "0.50 · unlocks easily"),
            ("balanced", "Balanced", "0.60 · recommended"),
            ("strict", "Strict", "0.72 · fewer false accepts"),
        ))
        self.presets.valueChanged.connect(self._preset_chosen)
        form.addWidget(self.presets)

        form.addWidget(_title("Match threshold", "h2"))
        threshold_row = QHBoxLayout()
        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(45, 85)
        self.threshold.valueChanged.connect(self._threshold_changed)
        threshold_row.addWidget(self.threshold, 1)
        self.threshold_value = QLabel("0.60")
        self.threshold_value.setObjectName("mono")
        self.threshold_value.setFixedWidth(48)
        threshold_row.addWidget(self.threshold_value)
        form.addLayout(threshold_row)
        self.threshold_note = _muted("")
        form.addWidget(self.threshold_note)

        form.addWidget(_title("Give up after", "h2"))
        timeout_row = QHBoxLayout()
        self.timeout = NumberField()
        self.timeout.setRange(15, 600)
        self.timeout.setSingleStep(15)
        self.timeout.setSuffix(" s")
        self.timeout.setFixedWidth(140)
        timeout_row.addWidget(self.timeout)
        timeout_row.addStretch(1)
        form.addLayout(timeout_row)

        buttons = QHBoxLayout()
        self.save = QPushButton("Save settings")
        self.save.setObjectName("primary")
        self.save.clicked.connect(self._save)
        buttons.addWidget(self.save)
        self.status = _muted("")
        buttons.addWidget(self.status, 1)
        form.addLayout(buttons)
        self._settings_task: Task | None = None

        layout.addWidget(card)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(inner))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.load()

    def load(self) -> None:
        settings = self.window.settings
        self.identity.clear()
        self.identity.addItem("Select a person…", None)
        placeholder = self.identity.model().item(0)
        if placeholder is not None:
            placeholder.setEnabled(False)
        people = backend.identity_summary()
        for person in people:
            self.identity.addItem(person["name"], person["name"])
        index = self.identity.findData(settings.identity)
        self.identity.setCurrentIndex(index if index > 0 else 0)
        self._selection_changed()

        self.threshold.setValue(int(round(settings.threshold * 100)))
        self._threshold_changed(self.threshold.value())
        self.timeout.setValue(int(settings.timeout))
        self.status.setText("")

    def _selection_changed(self, *_args) -> None:
        if hasattr(self, "save"):
            selected = self.identity.currentData()
            valid = isinstance(selected, str) and bool(selected)
            self.save.setEnabled(valid and self._settings_task is None)

    def _preset_chosen(self, key: str) -> None:
        value = self.PRESETS.get(key)
        if value is not None and value != self.threshold.value():
            self.threshold.setValue(value)
        else:
            self._threshold_changed(self.threshold.value())

    def _threshold_changed(self, value: int) -> None:
        score = value / 100
        self.threshold_value.setText(f"{score:.2f}")
        if score < 0.55:
            tone = "Loose - unlocks easily, and may eventually accept a lookalike."
        elif score < 0.68:
            tone = "Balanced - the default; the nearest sample must also agree."
        else:
            tone = "Strict - fewer false accepts, more retries in poor light."
        self.threshold_note.setText(tone)
        if hasattr(self, "presets"):
            match = next((name for name, preset in self.PRESETS.items() if preset == value), None)
            self.presets.set_value(match)

    def _save(self) -> None:
        selected = self.identity.currentData()
        if not isinstance(selected, str) or not selected:
            self.status.setText("Select exactly one person who may unlock.")
            self.status.setStyleSheet(f"color: {DANGER};")
            return

        candidate = backend.Settings(
            threshold=self.threshold.value() / 100,
            timeout=float(self.timeout.value()),
            identity=selected,
            camera=self.window.settings.camera,
        )
        was_running = backend.agent_running()

        self._settings_task = Task(
            lambda: backend.apply_settings(candidate, was_running), self
        )
        self._settings_task.done.connect(
            lambda ok, message: self._settings_finished(ok, message, candidate)
        )
        self.save.setEnabled(False)
        self.identity.setEnabled(False)
        self.status.setStyleSheet("")
        self.status.setText("Applying settings…")
        self._settings_task.start()

    def _settings_finished(self, ok: bool, message: str, candidate) -> None:
        self._settings_task = None
        saved = ok or message.startswith("Settings saved")
        if saved:
            self.window.settings = candidate
            self.window.camera.set_threshold(candidate.threshold)
            self.status.setStyleSheet(f"color: {ACCENT if ok else WARN};")
        else:
            self.status.setStyleSheet(f"color: {DANGER};")
        self.identity.setEnabled(True)
        self.status.setText(message)
        if not saved:
            self.load()
        else:
            self._selection_changed()


RecognitionPage = SettingsPage


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #
class LogsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(_title("Logs"))
        header.addStretch(1)
        self.source = Select()
        self.source.addItem("agent.log", "agent.log")
        self.source.addItem("provider.log", "provider.log")
        self.source.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.source)
        self.auto = CheckBox("Auto refresh")
        self.auto.setChecked(True)
        header.addWidget(self.auto)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.text, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        if self.auto.isChecked():
            self.refresh()

    def refresh(self) -> None:
        name = self.source.currentData()
        content = backend.log_text(name)
        scroll = self.text.verticalScrollBar()
        at_bottom = scroll.value() >= scroll.maximum() - 4
        self.text.setPlainText(content)
        if at_bottom:
            scroll.setValue(scroll.maximum())


class PasswordPage(QWidget):
    """Windows credential status and the existing verified password flow."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(14)
        layout.addWidget(_page_header(
            "Password",
            "Connect Glance to your existing Windows account.",
            2,
        ))

        self.group = SettingsGroup()
        self.credential = QLabel("Checking…")
        self.credential.setObjectName("muted")
        self.change = QPushButton("Set password…")
        self.change.setObjectName("primary")
        self.change.clicked.connect(self._set_password)
        self.row = SettingsRow("Windows credential", "Verified and encrypted on this PC", self.change)
        self.group.add_row(self.row)
        layout.addWidget(self.group)
        layout.addWidget(self.credential)
        layout.addWidget(_title("Windows lock screen", "h2"))
        layout.addWidget(_muted(
            "Enable only after enrolling and selecting a face and verifying your Windows password. "
            "Windows will ask for administrator approval before changing sign-in components. "
            "Your normal password and PIN sign-in remain available."
        ))
        self.unlock_status = _muted("Face unlock setup has not been checked.")
        layout.addWidget(self.unlock_status)
        controls = QHBoxLayout()
        self.enable_unlock = QPushButton("Enable Windows unlock")
        self.enable_unlock.setObjectName("primary")
        self.enable_unlock.clicked.connect(self._enable_unlock)
        controls.addWidget(self.enable_unlock)
        self.disable_unlock = QPushButton("Disable")
        self.disable_unlock.clicked.connect(self._disable_unlock)
        controls.addWidget(self.disable_unlock)
        self.reset_unlock = QPushButton("Reset Glance…")
        self.reset_unlock.clicked.connect(self._reset_unlock)
        controls.addWidget(self.reset_unlock)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addStretch(1)
        self._credential_task: Task | None = None
        self._unlock_task: Task | None = None
        self._account = backend.ACCOUNT_HINT

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._load_status()

    def _load_status(self) -> None:
        if self._credential_task is not None:
            return
        self.change.setEnabled(False)
        self.credential.setText("Checking stored credential…")
        self._credential_task = Task(
            lambda: (True, json.dumps(backend.credential_status())), self
        )
        self._credential_task.done.connect(self._status_loaded)
        self._credential_task.start()

    def _status_loaded(self, ok: bool, payload: str) -> None:
        self._credential_task = None
        self.change.setEnabled(True)
        if not ok:
            self.credential.setText(payload or "Could not read credential status.")
            return
        status = json.loads(payload)
        self._account = status.get("account") or backend.ACCOUNT_HINT
        if status["present"]:
            self.credential.setText(f"Stored for {status['account'] or 'this account'}")
            self.change.setText("Change password…")
        else:
            self.credential.setText("No Windows credential is stored yet.")
            self.change.setText("Set password…")

    def _set_password(self) -> None:
        dialog = PasswordDialog(self, self._account)
        if dialog.exec() == QDialog.Accepted:
            self._load_status()

    def _enable_unlock(self) -> None:
        self._run_unlock_operation("enable", backend.enable_windows_unlock)

    def _disable_unlock(self) -> None:
        self._run_unlock_operation("disable", backend.disable_windows_unlock)

    def _reset_unlock(self) -> None:
        answer = QMessageBox.question(
            self, "Reset Glance",
            "Disable face unlock and remove Glance's saved Windows credential and machine security state? "
            "Your enrolled face samples remain available in the app.",
        )
        if answer == QMessageBox.Yes:
            self._run_unlock_operation("reset", lambda: backend.disable_windows_unlock(reset=True))

    def _run_unlock_operation(self, label: str, operation) -> None:
        if self._unlock_task is not None:
            return
        self._set_unlock_controls(False)
        self.unlock_status.setStyleSheet("")
        self.unlock_status.setText(f"{label.capitalize()} Windows face unlock… approve or cancel the Windows prompt.")
        self._unlock_task = Task(operation, self)
        self._unlock_task.done.connect(lambda ok, message: self._unlock_finished(label, ok, message))
        self._unlock_task.start()

    def _unlock_finished(self, label: str, ok: bool, message: str) -> None:
        self._unlock_task = None
        self._set_unlock_controls(True)
        self.unlock_status.setStyleSheet(f"color: {ACCENT if ok else WARN if 'cancel' in message.lower() else DANGER};")
        self.unlock_status.setText(message)
        if label in {"disable", "reset"} and ok:
            self.credential.setText("No Windows credential is stored.")
            self.change.setText("Set password…")
        elif label == "enable" and ok:
            self.unlock_status.setText(message + " Restart Windows or lock the screen to use the provider.")

    def _set_unlock_controls(self, enabled: bool) -> None:
        for button in (self.enable_unlock, self.disable_unlock, self.reset_unlock):
            button.setEnabled(enabled)


class CameraPage(QWidget):
    """Explicit camera selection and opt-in preview."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(14)
        layout.addWidget(_page_header(
            "Camera",
            "Check your camera before your next sign-in.",
            3,
        ))
        self.group = SettingsGroup()
        self.camera_index = NumberField()
        self.camera_index.setRange(0, 5)
        self.camera_index.setFixedWidth(110)
        self.camera_index.setValue(window.settings.camera)
        self.group.add_row(SettingsRow("Camera device", "Change this if another camera is preferred.", self.camera_index))
        self.save_button = QPushButton("Save camera")
        self.save_button.clicked.connect(self._save_camera)
        self.group.add_row(SettingsRow("Default camera", trailing=self.save_button))
        layout.addWidget(self.group)
        self.preview_toggle = CheckBox("Show live preview")
        self.preview_toggle.setChecked(False)
        self.preview_toggle.toggled.connect(self._toggle_preview)
        layout.addWidget(self.preview_toggle)
        self.preview = Preview()
        self.preview.setMinimumHeight(200)
        self.preview.setMinimumWidth(0)
        layout.addWidget(self.preview, 1)
        self.status = _muted("")
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.camera_index.setValue(self.window.settings.camera)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.preview_toggle.setChecked(False)

    def _toggle_preview(self, enabled: bool) -> None:
        if enabled:
            self.window.camera.set_camera(self.camera_index.value())
            self.window.acquire_camera("camera-preview")
        else:
            self.window.release_camera("camera-preview")
            self.preview.clear()

    def _save_camera(self) -> None:
        self.window.settings.camera = self.camera_index.value()
        try:
            self.window.settings.save()
            if self.preview_toggle.isChecked():
                self.window.retry_camera("camera-preview")
            else:
                self.window.camera.set_camera(self.camera_index.value())
            self.status.setText("Camera preference saved.")
        except OSError as error:
            self.status.setText(f"Could not save camera preference: {error}")

    def on_preview(self, image, info) -> None:
        if self.isVisible() and self.preview_toggle.isChecked():
            self.preview.set_frame(image, info.faces, info.label, info.hint)

    def on_camera_failed(self, message: str) -> None:
        self.status.setText(message)
        self.preview.clear()
        self.preview_toggle.setChecked(False)


class AboutPage(QWidget):
    """App information and the existing diagnostic log viewer."""

    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(12)
        self.content = QWidget()
        content = QVBoxLayout(self.content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)
        content.addWidget(_page_header(
            "About",
            "Version, models, and diagnostics for this install.",
            5,
        ))
        info = SettingsGroup()
        info.add_row(SettingsRow(
            "Glance",
            f"Face unlock, on your own device.\nVersion {__version__} · Windows 11",
        ))
        content.addWidget(info)
        content.addWidget(_title("Face models", "h2"))
        self.models_button = QPushButton()
        self.models_button.clicked.connect(self._manage_models)
        self.models_row = SettingsRow("InsightFace buffalo_s", "", self.models_button)
        models = SettingsGroup()
        models.add_row(self.models_row)
        content.addWidget(models)
        self._refresh_models()
        content.addWidget(_title("Diagnostics", "h2"))
        self.open_logs = QPushButton("Open logs")
        self.open_logs.clicked.connect(self._show_logs)
        diagnostics = SettingsGroup()
        diagnostics.add_row(SettingsRow("Agent logs", "Review startup and recognition activity.", self.open_logs))
        content.addWidget(diagnostics)
        content.addStretch(1)
        self.logs = LogsPage(window)
        self.back_logs = QPushButton("← About")
        self.back_logs.clicked.connect(lambda: self.pages.setCurrentWidget(self.content))
        self.logs.layout().itemAt(0).layout().insertWidget(0, self.back_logs)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.content)
        self.pages.addWidget(self.logs)
        layout.addWidget(self.pages)

    def _refresh_models(self) -> None:
        if backend.models_ready():
            self.models_row.subtitle.setText(
                "Installed and checksum-verified in your local app data."
            )
            self.models_button.setText("Details")
        else:
            self.models_row.subtitle.setText(
                "Not downloaded yet. Face detection and recognition need them."
            )
            self.models_button.setText("Download")
        self.models_row.subtitle.setVisible(True)

    def _manage_models(self) -> None:
        if backend.models_ready():
            QMessageBox.information(
                self, "Glance",
                "The InsightFace buffalo_s models are installed.\n\n"
                f"{backend.PATHS.models_dir}\n\n"
                "InsightFace licenses these weights for non-commercial research use.",
            )
            return
        ModelsDialog(self).exec()
        self._refresh_models()

    def _show_logs(self) -> None:
        self.logs.refresh()
        self.pages.setCurrentWidget(self.logs)


# --------------------------------------------------------------------------- #
# password dialog
# --------------------------------------------------------------------------- #
class PasswordDialog(QDialog):
    def __init__(self, parent, account: str):
        super().__init__(parent)
        self.setWindowTitle("Windows password")
        self.setMinimumWidth(460)
        self._task: Task | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        layout.addWidget(_title("Windows password", "h2"))
        layout.addWidget(_muted(
            "Windows cannot unlock on a face check alone, so Glance replays your account "
            "password. It is verified against Windows first, then stored encrypted for "
            "this machine only."
        ))

        layout.addWidget(_muted("Account"))
        self.account = QLineEdit(account)
        layout.addWidget(self.account)

        layout.addWidget(_muted("Password"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.password)

        reveal = CheckBox("Show password")
        reveal.toggled.connect(
            lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        layout.addWidget(reveal)

        self.status = _muted("")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        self.submit = QPushButton("Verify & save")
        self.submit.setObjectName("primary")
        self.submit.clicked.connect(self._submit)
        self.submit.setDefault(True)
        buttons.addWidget(self.submit)
        layout.addLayout(buttons)

    def _submit(self) -> None:
        account = self.account.text().strip()
        password = self.password.text()
        if not account or not password:
            self.status.setText("Both fields are required.")
            return
        self._run(account, password)

    def _run(self, account: str, password: str) -> None:
        self.submit.setEnabled(False)
        self.cancel.setEnabled(False)
        self.status.setText("Waiting for the administrator prompt…")
        self.status.setStyleSheet("")

        self._task = Task(lambda: backend.store_password(account, password), self)
        self._task.done.connect(lambda ok, message: self._finished(ok, message, account, password))
        self._task.start()

    def _finished(self, ok: bool, message: str, account: str, password: str) -> None:
        self.submit.setEnabled(True)
        self.cancel.setEnabled(True)
        self.password.clear()
        if ok:
            self.status.setText("Password verified and stored. Continue to enable Windows unlock.")
            self.status.setStyleSheet(f"color: {ACCENT};")
            QTimer.singleShot(900, self.accept)
            return

        if "rejected" in message:
            self.status.setText("Windows rejected that password. It was not stored.")
            self.status.setStyleSheet(f"color: {DANGER};")
            return

        self.status.setText(message)
        self.status.setStyleSheet(f"color: {DANGER};")
