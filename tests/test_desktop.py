r"""Desktop-app regression tests. Run with .venv\Scripts\python.exe -m tests.test_desktop."""

from __future__ import annotations

import sys
import json
import importlib.util
import queue
import threading
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QWidget

import glance.agent.glance_agent as glance_agent
import glance.desktop.backend as backend
import glance.desktop.face_gate as face_gate
import glance.desktop.main as desktop_main
import glance.desktop.theme as theme
from glance.desktop.onboarding import FirstRunDialog
from glance.desktop.models_setup import ModelsDialog
from glance.desktop.settings_widgets import FloatingTabBar, SettingsGroup, SettingsRow
from glance.desktop.backend import Settings
from glance.desktop.camera import CameraWorker
from glance.desktop.face_gate import FaceGateDialog
from glance.desktop.pages import EnrollPage, PasswordDialog, SettingsPage
from glance.desktop.main import MainWindow


def wait_for(predicate, timeout_ms: int = 4000) -> bool:
    """Pump the event loop until predicate() holds. Wall-clock qWait alone is
    not enough on a loaded machine: the timer callback can still be queued when
    the fixed wait expires."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(20)
    return bool(predicate())


class ProbeWorker(CameraWorker):
    """Small real QThread that emits frames without touching camera hardware."""

    def __init__(self) -> None:
        super().__init__()
        self.emitted = threading.Event()
        self.count = 0
        self.count_lock = threading.Lock()

    def run(self) -> None:
        while not self._stop.is_set():
            with self.count_lock:
                self.count += 1
            self.emitted.set()
            self._stop.wait(0.01)


class CameraLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_camera_worker_can_restart_after_stop(self) -> None:
        worker = ProbeWorker()

        worker.start()
        self.assertTrue(worker.emitted.wait(1.0), "first worker run emitted no preview")
        worker.stop()
        self.assertTrue(worker.wait(1000), "first worker run did not stop")
        with worker.count_lock:
            first_count = worker.count
        worker.emitted.clear()

        worker.start()
        try:
            self.assertTrue(worker.emitted.wait(1.0), "worker emitted no preview after restart")
            with worker.count_lock:
                self.assertGreater(worker.count, first_count)
        finally:
            worker.stop()
            self.assertTrue(worker.wait(1000), "restarted worker did not stop")


    def test_capture_request_is_single_flight_and_requires_ready_worker(self) -> None:
        worker = CameraWorker()
        worker._ready = threading.Event()
        worker.isRunning = lambda: True

        self.assertFalse(worker.request_capture(), "capture before readiness must be rejected")
        worker._ready.set()
        self.assertTrue(worker.request_capture(), "first ready capture request must be accepted")
        self.assertFalse(worker.request_capture(), "a pending capture must not be coalesced")

    def test_gate_mode_withholds_matching_identity_without_proof_of_life(self) -> None:
        class FakeCamera:
            def read(self):
                return True, np.zeros((100, 100, 3), dtype=np.uint8)

            def release(self) -> None:
                pass

        class FakePipeline:
            def __init__(self, threshold: float) -> None:
                face = SimpleNamespace(bbox=(10, 10, 70, 70), area=3600)
                self.detector = SimpleNamespace(detect=lambda frame: [face])
                self.aligner = SimpleNamespace(align=lambda frame, face: np.zeros((112, 112, 3), np.uint8))
                self.embedder = SimpleNamespace(embed=lambda aligned: np.ones(8))
                self.store = SimpleNamespace(
                    load=lambda: None,
                    best_match=lambda embedding, threshold: (
                        SimpleNamespace(name="Sampath"), 0.9, 0.9
                    ),
                )

            def open_camera(self, index: int):
                return FakeCamera()

        worker = CameraWorker(pipeline_factory=FakePipeline)
        results: queue.Queue = queue.Queue()
        worker.gate.connect(
            lambda name, score, hint: results.put((name, score, hint)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        try:
            self.assertTrue(worker._ready.wait(1.0), "camera worker never became ready")
            worker.set_gate(True)
            for _ in range(4):
                name, score, hint = results.get(timeout=2.0)
                self.assertEqual(name, "")
                self.assertEqual(score, 0.0)
                self.assertTrue(hint)
            worker.set_gate(False)
            while not results.empty():
                results.get_nowait()
            with self.assertRaises(queue.Empty):
                results.get(timeout=0.8)
        finally:
            worker.set_gate(False)
            worker.stop()
            self.assertTrue(worker.wait(1000), "camera worker did not stop")

    def test_worker_signals_ready_and_returns_one_capture_result(self) -> None:
        class FakeCamera:
            def read(self):
                return True, np.zeros((80, 80, 3), dtype=np.uint8)

            def release(self) -> None:
                pass

        class FakePipeline:
            def __init__(self, threshold: float) -> None:
                self.detector = SimpleNamespace(detect=lambda frame: [])
                self.store = SimpleNamespace(load=lambda: None)

            def open_camera(self, index: int):
                return FakeCamera()

        worker = CameraWorker(pipeline_factory=FakePipeline)
        results: queue.Queue = queue.Queue()
        worker.captured.connect(lambda capture: results.put(capture), Qt.ConnectionType.DirectConnection)
        worker.start()
        try:
            self.assertTrue(worker._ready.wait(1.0), "camera worker never became ready")
            self.assertTrue(worker.request_capture())
            self.assertFalse(worker.request_capture(), "rapid duplicate request must be refused")
            capture = results.get(timeout=1.0)
            self.assertFalse(capture.ok)
            self.assertEqual(capture.message, "No face in view")
            self.assertTrue(worker.request_capture(), "result must release the pending slot")
            self.assertEqual(results.get(timeout=1.0).message, "No face in view")
        finally:
            worker.stop()
            self.assertTrue(worker.wait(1000))

    def test_changing_camera_index_reopens_device_on_worker_thread(self) -> None:
        indices = []
        switched = threading.Event()

        class FakeCamera:
            def read(self):
                return True, np.zeros((80, 80, 3), dtype=np.uint8)

            def release(self):
                pass

        class FakePipeline:
            def __init__(self, threshold: float):
                self.detector = SimpleNamespace(detect=lambda frame: [])
                self.store = SimpleNamespace(load=lambda: None)

            def open_camera(self, index: int):
                indices.append(index)
                if index == 1:
                    switched.set()
                return FakeCamera()

        worker = CameraWorker(pipeline_factory=FakePipeline)
        worker.start()
        try:
            self.assertTrue(worker._ready.wait(1.0))
            worker.set_camera(1)
            self.assertTrue(switched.wait(1.0), "worker did not reopen the selected device")
            self.assertEqual(indices[:2], [0, 1])
        finally:
            worker.stop()
            self.assertTrue(worker.wait(1000))


class AdaptiveThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_settings_row_accepts_a_subtitle_set_after_construction(self) -> None:
        row = SettingsRow("InsightFace buffalo_s", "")
        row.subtitle.setText("Installed and checksum-verified.")
        row.subtitle.setVisible(True)
        self.assertFalse(row.subtitle.isHidden())
        self.assertEqual(row.subtitle.text(), "Installed and checksum-verified.")

    def test_about_signin_controls_do_not_collapse_in_a_short_viewport(self) -> None:
        from glance.desktop.pages import AboutPage
        page = AboutPage(SimpleNamespace())
        page.setStyleSheet(theme.resolve_theme("dark").stylesheet)
        page.resize(800, 500)
        page.show()
        try:
            self.app.processEvents()
            for button in (page.identify_account_button, page.check_password_button,
                           page.signin_options_button):
                self.assertGreaterEqual(button.height(), button.minimumSizeHint().height())
            page.account_status.setText("Microsoft account\nAccount: person@example.com\n" +
                                        "Long account guidance. " * 30)
            self.app.processEvents()
            self.assertTrue(page.account_status.wordWrap())
            self.assertTrue(wait_for(lambda: page.account_status.height() >=
                                     page.account_status.heightForWidth(page.account_status.width())))
        finally:
            page.close()

    def test_light_and_dark_themes_have_distinct_surfaces(self) -> None:
        self.assertTrue(hasattr(theme, "resolve_theme"), "theme resolver is not implemented")
        light = theme.resolve_theme("light")
        dark = theme.resolve_theme("dark")
        self.assertNotEqual(light.background, dark.background)
        self.assertNotEqual(light.foreground, dark.foreground)
        self.assertIn("QPushButton#primary", light.stylesheet)
        self.assertIn("QPushButton#primary", dark.stylesheet)
        self.assertNotIn("blur", light.stylesheet.lower())

    def test_app_icon_loads_the_approved_artwork(self) -> None:
        icon = theme.app_icon()
        self.assertFalse(icon.isNull())
        self.assertIs(icon, theme.app_icon(), "icon should be reused across windows")
        self.assertTrue((Path(__file__).resolve().parents[1] / "assets" / "GlanceIcon.png").is_file())

    def test_floating_navigation_widget_is_available(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("glance.desktop.settings_widgets"))

    def test_floating_tab_bar_emits_selected_page_index(self) -> None:
        bar = FloatingTabBar(("General", "Face", "Password"))
        selected = []
        bar.indexChanged.connect(selected.append)
        bar.buttons[2].click()
        self.assertEqual(bar.count(), 3)
        self.assertEqual(selected, [2])
        self.assertTrue(bar.buttons[2].isChecked())
        group = SettingsGroup()
        group.add_row(SettingsRow("Face", "Enrolled identities"))
        self.assertEqual(group.column.count(), 1)

    def test_main_window_has_six_sidebar_pages_and_preserves_window_size(self) -> None:
        window = MainWindow()
        self.assertEqual(
            [button.text() for button in window.nav.buttons],
            ["General", "Your Face", "Password", "Camera", "Recognition", "About"],
        )
        self.assertEqual(window.stack.count(), 6)
        self.assertFalse(window.camera.isRunning())
        self.assertFalse(window.dashboard.camera_toggle.isChecked())
        self.assertFalse(window.camera_page.preview_toggle.isChecked())
        original_size = window.size()
        window.nav.buttons[4].click()
        self.assertEqual(window.stack.currentIndex(), 4)
        self.assertEqual(window.size(), original_size)
        self.assertTrue(window.nav.buttons[4].isChecked())
        self.assertFalse(window.nav.buttons[0].isChecked())
        window.close()

    def test_sidebar_groups_and_window_controls_match_the_glass_shell(self) -> None:
        window = MainWindow()
        sections = [
            label.text()
            for label in window.nav.findChildren(QLabel)
            if label.objectName() == "navSection"
        ]
        self.assertEqual(sections, ["Authentication", "Glance"])
        self.assertEqual(window.nav.count(), 6)
        self.assertTrue(hasattr(window, "window_controls"))
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        window.close()

    def test_matching_profile_tiles_drive_the_threshold(self) -> None:
        window = MainWindow()
        page = window.settings_page
        page.presets.tiles["strict"].click()
        self.assertEqual(page.threshold.value(), 72)
        self.assertTrue(page.presets.tiles["strict"].isChecked())
        page.threshold.setValue(63)
        self.assertIsNone(page.presets.value())
        self.assertEqual(page.threshold_value.text(), "0.63")
        window.close()

    def test_compact_onboarding_module_is_available(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("glance.desktop.onboarding"))

    def test_onboarding_persists_completion_only_after_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / "settings.ini"), QSettings.IniFormat)
            settings.clear()
            window = MainWindow()
            dialog = FirstRunDialog(window, settings)
            self.assertEqual(dialog.stack.count(), 5)
            self.assertFalse(FirstRunDialog.is_completed(settings))
            for _ in range(dialog.stack.count()):
                dialog._next()
            self.assertTrue(FirstRunDialog.is_completed(settings))
            window.close()

    @patch.object(desktop_main.backend, "identity_summary", return_value=[])
    def test_dashboard_routes_uninstalled_agent_to_in_app_setup(self, _identities) -> None:
        window = MainWindow()
        window.dashboard._apply_snapshot({
            "task": {"present": False, "enabled": False},
            "running": False,
            "credential": {"present": False, "account": ""},
            "identities": [],
            "unlock": "never",
        })

        self.assertEqual(window.dashboard.status_title.text(), "Windows unlock not enabled")
        self.assertIn("Password", window.dashboard.status_detail.text())
        self.assertNotIn("install.ps1", window.dashboard.status_detail.text())
        self.assertEqual(window.dashboard.pause_button.text(), "Set up Windows unlock")
        self.assertTrue(window.dashboard.pause_button.isEnabled())
        self.assertFalse(window.dashboard.autostart.isEnabled())
        window.dashboard.pause_button.click()
        self.assertEqual(window.stack.currentIndex(), 2)
        window.close()

    def test_dashboard_refresh_does_not_wait_for_slow_windows_state(self) -> None:
        window = MainWindow()

        def slow_task_state():
            time.sleep(0.25)
            return {"present": False}

        with (
            patch("glance.desktop.pages.backend.task_state", side_effect=slow_task_state),
            patch("glance.desktop.pages.backend.agent_running", return_value=False),
            patch("glance.desktop.pages.backend.credential_status", return_value={"present": False, "account": ""}),
            patch("glance.desktop.pages.backend.identity_summary", return_value=[]),
            patch("glance.desktop.pages.backend.last_unlock", return_value="never"),
        ):
            started = time.perf_counter()
            window.dashboard.refresh()
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.1, "UI refresh blocked on Windows status work")
            task = window.dashboard._refresh_task
            self.assertTrue(task.wait(1500))
            self.app.processEvents()
        window.close()


class AgentIdentityTests(unittest.TestCase):
    class FakeCamera:
        def isOpened(self) -> bool:
            return True

        def release(self) -> None:
            pass

    class FakePipeline:
        def __init__(self, *args, **kwargs) -> None:
            self.store = SimpleNamespace(identities=[SimpleNamespace(name="Sampath")])
            self.open_calls = 0

        def open_camera(self, index: int = 0):
            self.open_calls += 1
            return AgentIdentityTests.FakeCamera()

    def _main_verifier(self, config_bytes: bytes, cli_identity: str):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            (data / "agent_config.json").write_bytes(config_bytes)
            seen = []

            class Watcher:
                def __init__(self, verifier) -> None:
                    seen.append(verifier)

                def run(self) -> int:
                    return 0

            with (
                patch.object(glance_agent, "ROOT", root),
                patch.object(glance_agent, "claim_single_instance", return_value=True),
                patch.object(glance_agent, "SessionWatcher", Watcher),
                patch.object(sys, "argv", ["glance_agent.py", "--identity", cli_identity]),
            ):
                self.assertEqual(glance_agent.main(), 0)
            self.assertEqual(len(seen), 1)
            return seen[0]

    def _main_identity(self, config_bytes: bytes, cli_identity: str) -> str | None:
        return self._main_verifier(config_bytes, cli_identity).identity

    def test_settings_identity_overrides_stale_task_argument(self) -> None:
        content = json.dumps({"identity": "Anu", "any_identity": False}).encode("utf-8")
        self.assertEqual(self._main_identity(content, "Sampath"), "Anu")

    def test_empty_config_identity_does_not_fall_back_to_task_argument(self) -> None:
        content = json.dumps({"identity": "", "any_identity": False}).encode("utf-8")
        self.assertIsNone(self._main_identity(content, "Sampath"))

    def test_corrupt_existing_config_does_not_fall_back_to_task_argument(self) -> None:
        self.assertIsNone(self._main_identity(b"{broken json", "Sampath"))

    def test_non_object_config_does_not_fall_back_to_task_argument(self) -> None:
        self.assertIsNone(self._main_identity(b"[]", "Sampath"))

    def test_agent_config_accepts_windows_utf8_bom(self) -> None:
        content = json.dumps({"identity": "Anu", "any_identity": False}).encode("utf-8-sig")
        self.assertEqual(self._main_identity(content, "Sampath"), "Anu")

    def _grant_result(self, face_name: str, identity: str | None = None,
                      any_identity: bool = False, identities=("Sampath", "Anu")):
        import numpy as np

        stop = threading.Event()

        class FaceCamera:
            def __init__(self) -> None:
                self.reads = 0

            def isOpened(self) -> bool:
                return True

            def read(self):
                self.reads += 1
                if self.reads >= (1 if face_name == "Sampath" else 4):
                    stop.set()
                return True, np.zeros((100, 100, 3), dtype=np.uint8)

            def release(self) -> None:
                pass

        class Pipeline:
            def __init__(self, *args, **kwargs) -> None:
                people = [SimpleNamespace(name=name) for name in identities]
                matched = next((person for person in people if person.name == face_name), None)
                self.store = SimpleNamespace(
                    identities=people,
                    best_match=lambda embedding, threshold: (
                        (matched, 0.9, 0.9) if matched is not None else None
                    ),
                )
                self.camera = FaceCamera()

            def open_camera(self, index: int = 0):
                return self.camera

            def embedding_for(self, frame):
                return np.ones(8), SimpleNamespace(
                    area=3600, bbox=(20, 20, 80, 80)
                ), None

        class Evaluator:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def reset(self) -> None:
                pass

            def observe(self, readings):
                return SimpleNamespace(decision=glance_agent.lv.Decision.CONFIRMED)

        def write_grant():
            stop.set()
            return "grant"

        with (
            patch.object(glance_agent, "GlancePipeline", return_value=Pipeline()),
            patch.object(glance_agent.lv, "LivenessEvaluator", Evaluator),
            patch.object(glance_agent.lv, "extract_glare", return_value=None),
            patch.object(glance_agent.lv, "detect_device_rectangle", return_value=None),
            patch.object(glance_agent.lv, "readings", return_value={}),
            patch.object(glance_agent.grant_mod, "write_grant", side_effect=write_grant) as writer,
        ):
            result = glance_agent.Verifier(
                identity=identity, any_identity=any_identity,
                require_stable=1, timeout=2,
            ).run(stop)
        return result, writer

    def test_only_selected_face_can_write_a_grant(self) -> None:
        for selected_identity in ("Sampath", "Anu"):
            for face_name in ("Sampath", "Anu"):
                with self.subTest(selected=selected_identity, face=face_name):
                    result, writer = self._grant_result(face_name, identity=selected_identity)
                    if face_name == selected_identity:
                        self.assertEqual(result, "granted")
                        writer.assert_called_once()
                    else:
                        self.assertNotEqual(result, "granted")
                        writer.assert_not_called()

    def test_anyone_mode_grants_for_any_enrolled_face(self) -> None:
        result, writer = self._grant_result("Anu", identity=None, any_identity=True)
        self.assertEqual(result, "granted")
        writer.assert_called_once()

    def test_anyone_mode_fails_closed_with_no_enrolled_faces(self) -> None:
        result, writer = self._grant_result("Sampath", identity=None,
                                            any_identity=True, identities=())
        self.assertEqual(result, "error: no enrolled identities")
        writer.assert_not_called()

    def test_anyone_config_sets_a_wildcard_verifier(self) -> None:
        content = json.dumps({"any_identity": True, "identity": ""}).encode("utf-8")
        verifier = self._main_verifier(content, "Sampath")
        self.assertTrue(verifier.any_identity)
        self.assertIsNone(verifier.identity)

    def test_specific_config_keeps_one_identity(self) -> None:
        content = json.dumps({"any_identity": False, "identity": "Anu"}).encode("utf-8")
        verifier = self._main_verifier(content, "Sampath")
        self.assertFalse(verifier.any_identity)
        self.assertEqual(verifier.identity, "Anu")

    def test_no_selection_fails_closed_even_with_one_enrolled_identity(self) -> None:
        pipeline = self.FakePipeline()
        stop = threading.Event()
        stop.set()
        with patch.object(glance_agent, "GlancePipeline", return_value=pipeline):
            result = glance_agent.Verifier(identity=None).run(stop)

        self.assertEqual(result, "error: no unlock identity selected")
        self.assertEqual(pipeline.open_calls, 0, "a missing selection must fail before camera access")

    def test_unknown_selection_fails_closed_before_camera_access(self) -> None:
        pipeline = self.FakePipeline()
        stop = threading.Event()
        stop.set()
        with patch.object(glance_agent, "GlancePipeline", return_value=pipeline):
            result = glance_agent.Verifier(identity="Unknown").run(stop)

        self.assertEqual(result, "error: selected identity is not enrolled")
        self.assertEqual(pipeline.open_calls, 0, "an invalid selection must not start verification")


class SettingsIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def make_page(self, selected: str, any_identity: bool = False) -> SettingsPage:
        window = SimpleNamespace(settings=Settings(identity=selected,
                                                   any_identity=any_identity))
        page = SettingsPage(window)
        with (
            patch("glance.desktop.pages.backend.identity_summary", return_value=[
                {"name": "Sampath", "samples": 9},
                {"name": "Anu", "samples": 12},
            ]),
            patch("glance.desktop.pages.backend.task_state", return_value={"enabled": True}),
        ):
            page.load()
        return page

    def test_anyone_enrolled_is_the_default_scope(self) -> None:
        page = self.make_page("", any_identity=True)
        self.assertEqual(page.identity.currentData(), "")
        self.assertEqual(page.identity.currentText(), "Anyone enrolled")
        self.assertTrue(page.save.isEnabled())
        self.assertIn("Anyone enrolled", page.scope_note.text())

    def test_one_person_scope_is_preserved(self) -> None:
        page = self.make_page("Sampath")
        self.assertEqual(page.identity.currentData(), "Sampath")
        self.assertIn("Only Sampath", page.scope_note.text())

    def test_saving_anyone_scope_persists_an_empty_identity(self) -> None:
        page = self.make_page("Sampath", any_identity=True)
        seen = {}

        def apply(candidate, _running):
            seen["candidate"] = candidate
            return True, "Settings saved."

        class FakeTask:
            def __init__(self, function, _parent=None):
                self.function = function
                self.done = SimpleNamespace(connect=lambda _callback: None)

            def start(self):
                self.function()

        with (
            patch("glance.desktop.pages.Task", FakeTask),
            patch("glance.desktop.pages.backend.agent_running", return_value=False),
            patch("glance.desktop.pages.backend.apply_settings", side_effect=apply),
        ):
            page._save()
        self.assertTrue(seen["candidate"].any_identity)
        self.assertEqual(seen["candidate"].identity, "")

    def test_saving_a_person_scope_persists_that_identity(self) -> None:
        page = self.make_page("Sampath", any_identity=False)
        seen = {}

        def apply(candidate, _running):
            seen["candidate"] = candidate
            return True, "Settings saved."

        class FakeTask:
            def __init__(self, function, _parent=None):
                self.function = function
                self.done = SimpleNamespace(connect=lambda _callback: None)

            def start(self):
                self.function()

        with (
            patch("glance.desktop.pages.Task", FakeTask),
            patch("glance.desktop.pages.backend.agent_running", return_value=False),
            patch("glance.desktop.pages.backend.apply_settings", side_effect=apply),
        ):
            page._save()
        self.assertFalse(seen["candidate"].any_identity)
        self.assertEqual(seen["candidate"].identity, "Sampath")

    def test_owner_change_stops_agent_before_saving_then_restarts(self) -> None:
        events = []
        def stop():
            events.append("stop")
            return True, "stopped"

        def save():
            events.append("save")

        def start():
            events.append("start")
            return True, "started"

        with (
            patch("glance.desktop.backend.agent_running", side_effect=[False, False, True, True]),
            patch("glance.desktop.backend.stop_agent", side_effect=stop),
            patch("glance.desktop.backend.start_agent", side_effect=start),
        ):
            ok, message = backend.apply_settings(SimpleNamespace(save=save), True)

        self.assertTrue(ok, message)
        self.assertEqual(events[:3], ["stop", "save", "start"])

    def test_failed_stop_does_not_save_new_unlock_identity(self) -> None:
        events = []
        candidate = SimpleNamespace(save=lambda: events.append("save"))
        with (
            patch("glance.desktop.backend.stop_agent", return_value=(False, "denied")),
            patch("glance.desktop.backend.start_agent", side_effect=lambda: events.append("start")),
        ):
            ok, message = backend.apply_settings(candidate, True)
        self.assertFalse(ok)
        self.assertIn("settings were not changed", message)
        self.assertEqual(events, [])

    def test_settings_round_trip_keeps_selected_person_and_tuning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "agent_config.json"
            with patch("glance.desktop.backend.CONFIG_PATH", config_path):
                expected = Settings(threshold=0.72, timeout=120.0, identity="Sampath", camera=1)
                expected.save()
                actual = Settings.load()
        self.assertEqual(actual, expected)

    def test_bom_settings_loads_without_losing_selected_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "agent_config.json"
            config_path.write_text(
                '\ufeff{"identity":"Sampath","threshold":0.72,"timeout":120,"camera":1}',
                encoding="utf-8",
            )
            with patch("glance.desktop.backend.CONFIG_PATH", config_path):
                actual = Settings.load()
        self.assertEqual(actual.identity, "Sampath")
        self.assertEqual(actual.threshold, 0.72)

    def test_removing_selected_identity_stops_agent_and_clears_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "agent_config.json"
            identity_path = root / "identities.json"
            config_path.write_text('{"identity":"Sampath"}', encoding="utf-8")
            identity_path.write_text(
                '{"identities":[{"name":"Sampath","samples":[[1,2]]}]}',
                encoding="utf-8",
            )
            with (
                patch("glance.desktop.backend.CONFIG_PATH", config_path),
                patch("glance.desktop.backend.IDENTITIES_PATH", identity_path),
                patch("glance.desktop.backend.agent_running", side_effect=[True, False, False]),
                patch("glance.desktop.backend.stop_agent", return_value=(True, "stopped")) as stop,
                patch("glance.desktop.backend.clear_pending_grant", return_value=True) as clear_grant,
            ):
                ok, message = backend.delete_identity("Sampath")
                settings = Settings.load()
        self.assertTrue(ok, message)
        stop.assert_called_once()
        self.assertEqual(settings.identity, "")
        self.assertIn("face unlock is paused", message.lower())
        clear_grant.assert_called_once()

    def test_selected_identity_is_not_removed_if_pending_grant_cannot_be_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "agent_config.json"
            identity_path = root / "identities.json"
            config_path.write_text('{"identity":"Sampath"}', encoding="utf-8")
            original_identity = '{"identities":[{"name":"Sampath","samples":[[1,2]]}]}'
            identity_path.write_text(original_identity, encoding="utf-8")
            with (
                patch("glance.desktop.backend.CONFIG_PATH", config_path),
                patch("glance.desktop.backend.IDENTITIES_PATH", identity_path),
                patch("glance.desktop.backend.agent_running", return_value=False),
                patch("glance.desktop.backend.clear_pending_grant", return_value=False),
            ):
                ok, message = backend.delete_identity("Sampath")
                identity_after = json.loads(identity_path.read_text(encoding="utf-8"))
                config_after = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertFalse(ok)
        self.assertIn("grant", message.lower())
        self.assertEqual(identity_after["identities"][0]["name"], "Sampath")
        self.assertEqual(config_after["identity"], "Sampath")

    def test_removing_unselected_identity_keeps_owner_and_does_not_stop_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "agent_config.json"
            identity_path = root / "identities.json"
            config_path.write_text('{"identity":"Sampath"}', encoding="utf-8")
            identity_path.write_text(
                '{"identities":[{"name":"Sampath","samples":[[1,2]]},'
                '{"name":"Anu","samples":[[3,4]]}]}', encoding="utf-8",
            )
            with (
                patch("glance.desktop.backend.CONFIG_PATH", config_path),
                patch("glance.desktop.backend.IDENTITIES_PATH", identity_path),
                patch("glance.desktop.backend.agent_running", return_value=True),
                patch("glance.desktop.backend.stop_agent") as stop,
            ):
                ok, message = backend.delete_identity("Anu")
                settings = Settings.load()
        self.assertTrue(ok, message)
        stop.assert_not_called()
        self.assertEqual(settings.identity, "Sampath")

    def test_removing_missing_identity_does_not_pause_protection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "agent_config.json"
            identity_path = root / "identities.json"
            config_path.write_text('{"identity":"Sampath"}', encoding="utf-8")
            identity_path.write_text('{"identities":[]}', encoding="utf-8")
            with (
                patch("glance.desktop.backend.CONFIG_PATH", config_path),
                patch("glance.desktop.backend.IDENTITIES_PATH", identity_path),
                patch("glance.desktop.backend.agent_running", return_value=True),
                patch("glance.desktop.backend.stop_agent") as stop,
            ):
                ok, message = backend.delete_identity("Sampath")
        self.assertFalse(ok)
        self.assertIn("no identity named", message)
        stop.assert_not_called()


class FaceGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

        def stop_animation_loader() -> None:
            import glance.desktop.capture_widgets as capture_widgets
            capture_widgets._stop_loader()

        cls.addClassCleanup(stop_animation_loader)

    class FakeCamera(QObject):
        gate = Signal(str, float, str)
        failed = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.gate_enabled = False
            self.gate_calls: list[bool] = []

        def set_gate(self, active: bool) -> None:
            self.gate_enabled = active
            self.gate_calls.append(active)

    def make_dialog(self, camera, threshold: float = 0.60):
        events: list = []
        parent = QWidget()
        dialog = FaceGateDialog(
            parent, "Password", camera=camera, threshold=threshold,
            acquire=lambda: events.append("acquire"),
            release=lambda: events.append("release"),
            restart=lambda owner: events.append(("restart", owner)),
        )
        self.addCleanup(parent.deleteLater)
        self.addCleanup(dialog.close)
        return dialog, events, parent

    def show_dialog(self, dialog, *, people=None, models: bool = True,
                    timeout: float = face_gate.SCAN_SECONDS) -> None:
        dialog._scan_timer.setInterval(int(timeout * 1000))
        if people is None:
            people = [{"name": "Sampath", "samples": 9}]
        with (
            patch.object(face_gate.backend, "identity_summary", return_value=people),
            patch.object(face_gate.backend, "models_ready", return_value=models),
        ):
            dialog.show()
            QTest.qWait(20)

    def test_three_stable_matches_accept_the_dialog(self) -> None:
        camera = self.FakeCamera()
        dialog, events, _parent = self.make_dialog(camera)
        with patch.object(face_gate, "SUCCESS_HOLD_MS", 0):
            self.show_dialog(dialog)
            self.assertTrue(dialog.is_scanning())
            self.assertTrue(camera.gate_enabled)
            self.assertEqual(events, ["acquire"])
            for _ in range(2):
                camera.gate.emit("Sampath", 0.91, "")
                self.assertTrue(dialog.is_scanning())
            camera.gate.emit("Sampath", 0.91, "")
            QTest.qWait(100)
            self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.matched_name, "Sampath")
        self.assertEqual(dialog.animation.state, "success")
        self.assertFalse(camera.gate_enabled)
        self.assertEqual(events, ["acquire", "release"])

    def test_a_low_score_or_a_different_face_breaks_the_streak(self) -> None:
        camera = self.FakeCamera()
        dialog, _events, _parent = self.make_dialog(camera, threshold=0.70)
        # A scan window long enough that it cannot expire while the dialog is
        # starting up: the assertions below are about streak bookkeeping, not
        # the timer. A short window made this test fail intermittently, because
        # on a loaded machine the timeout fired before the gate signals were
        # delivered and a finished scan ignores them.
        self.show_dialog(dialog, timeout=600.0)
        camera.gate.emit("Sampath", 0.91, "")
        camera.gate.emit("Sampath", 0.91, "")
        self.assertEqual(dialog.streak(), 2)
        camera.gate.emit("Anu", 0.95, "")
        self.assertEqual(dialog.streak(), 1)
        camera.gate.emit("Anu", 0.44, "")
        self.assertEqual(dialog.streak(), 0)
        camera.gate.emit("Anu", 0.95, "")
        camera.gate.emit("Anu", 0.95, "")
        # Force the remaining scan window to expire and check the timeout path.
        dialog._scan_timer.start(20)
        self.assertTrue(wait_for(lambda: not dialog.is_scanning()))
        self.assertEqual(dialog.matched_name, "")
        self.assertEqual(dialog.animation.state, "failure")
        self.assertTrue(dialog.retry_visible())

    def test_timeout_fails_then_retry_scans_again(self) -> None:
        camera = self.FakeCamera()
        dialog, events, _parent = self.make_dialog(camera)
        with patch.object(face_gate, "SUCCESS_HOLD_MS", 0):
            self.show_dialog(dialog, timeout=0.08)
            self.assertTrue(wait_for(lambda: not dialog.is_scanning()))
            self.assertTrue(dialog.retry_visible())
            self.assertIn("verify", dialog.status.text().lower())
            self.assertFalse(camera.gate_enabled)
            self.assertEqual(events, ["acquire", "release"])

            with (
                patch.object(face_gate.backend, "identity_summary",
                             return_value=[{"name": "Sampath", "samples": 9}]),
                patch.object(face_gate.backend, "models_ready", return_value=True),
            ):
                dialog.retry.click()
                QTest.qWait(20)
                self.assertTrue(dialog.is_scanning())
                self.assertTrue(camera.gate_enabled)
                self.assertIn(("restart", "face-gate"), events)
                for _ in range(3):
                    camera.gate.emit("Sampath", 0.94, "")
                QTest.qWait(100)
                self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.matched_name, "Sampath")

    def test_missing_models_fail_closed_without_starting_the_camera(self) -> None:
        camera = self.FakeCamera()
        dialog, events, _parent = self.make_dialog(camera)
        self.show_dialog(dialog, models=False)
        self.assertFalse(dialog.is_scanning())
        self.assertFalse(dialog.retry_visible())
        self.assertIn("models", dialog.status.text().lower())
        self.assertEqual(events, [])
        self.assertFalse(camera.gate_enabled)

    def test_no_enrolled_faces_fails_closed_without_starting_the_camera(self) -> None:
        camera = self.FakeCamera()
        dialog, events, _parent = self.make_dialog(camera)
        self.show_dialog(dialog, people=[])
        self.assertFalse(dialog.is_scanning())
        self.assertFalse(dialog.retry_visible())
        self.assertIn("enrolled", dialog.status.text().lower())
        self.assertEqual(events, [])
        self.assertFalse(camera.gate_enabled)

    def test_camera_failure_blocks_the_page_and_offers_retry(self) -> None:
        camera = self.FakeCamera()
        dialog, _events, _parent = self.make_dialog(camera)
        self.show_dialog(dialog)
        camera.failed.emit("the camera is busy")
        self.assertFalse(dialog.is_scanning())
        self.assertTrue(dialog.retry_visible())
        self.assertIn("camera", dialog.status.text().lower())
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertFalse(camera.gate_enabled)


class NavigationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    class FakeGate:
        calls: list[str] = []
        result = QDialog.Accepted

        def __init__(self, _parent, purpose: str):
            type(self).calls.append(purpose)

        def exec(self) -> int:
            return type(self).result

    def setUp(self) -> None:
        self.FakeGate.calls = []
        self.FakeGate.result = QDialog.Accepted

    def window(self) -> MainWindow:
        return MainWindow()

    def test_password_opens_after_an_accepted_face_check(self) -> None:
        window = self.window()
        with (
            patch.object(desktop_main, "FaceGateDialog", self.FakeGate),
            patch.object(desktop_main.backend, "identity_summary",
                         return_value=[{"name": "Sampath", "samples": 9}]),
        ):
            window.nav.buttons[2].click()
        self.assertEqual(window.stack.currentIndex(), 2)
        self.assertEqual(self.FakeGate.calls, ["Password"])
        window.close()

    def test_cancelled_face_check_keeps_the_previous_page(self) -> None:
        window = self.window()
        self.FakeGate.result = QDialog.Rejected
        with (
            patch.object(desktop_main, "FaceGateDialog", self.FakeGate),
            patch.object(desktop_main.backend, "identity_summary",
                         return_value=[{"name": "Sampath", "samples": 9}]),
        ):
            window.nav.buttons[2].click()
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertTrue(window.nav.buttons[0].isChecked())
        self.assertFalse(window.nav.buttons[2].isChecked())
        window.close()

    def test_face_check_is_skipped_before_anyone_is_enrolled(self) -> None:
        window = self.window()
        with (
            patch.object(desktop_main, "FaceGateDialog", self.FakeGate),
            patch.object(desktop_main.backend, "identity_summary", return_value=[]),
        ):
            window.nav.buttons[2].click()
        self.assertEqual(window.stack.currentIndex(), 2)
        self.assertEqual(self.FakeGate.calls, [])
        window.close()

    def test_your_face_requires_a_stored_password_first(self) -> None:
        window = self.window()
        with (
            patch.object(desktop_main, "FaceGateDialog", self.FakeGate),
            patch.object(desktop_main.backend, "identity_summary",
                         return_value=[{"name": "Sampath", "samples": 9}]),
            patch.object(desktop_main.backend, "credential_status",
                         return_value={"present": False, "account": ""}),
            patch.object(desktop_main.QMessageBox, "information") as inform,
        ):
            window.nav.buttons[1].click()
        self.assertEqual(window.stack.currentIndex(), 2)
        self.assertEqual(self.FakeGate.calls, ["Password"])
        inform.assert_called_once()
        self.assertIn("password", inform.call_args.args[2].lower())
        window.close()

    def test_your_face_opens_after_password_and_face_check(self) -> None:
        window = self.window()
        with (
            patch.object(desktop_main, "FaceGateDialog", self.FakeGate),
            patch.object(desktop_main.backend, "identity_summary",
                         return_value=[{"name": "Sampath", "samples": 9}]),
            patch.object(desktop_main.backend, "credential_status",
                         return_value={"present": True, "account": "PC\\sampa"}),
        ):
            window.nav.buttons[1].click()
        self.assertEqual(window.stack.currentIndex(), 1)
        self.assertEqual(self.FakeGate.calls, ["Your Face"])
        window.close()

    def test_dashboard_password_shortcut_is_gated(self) -> None:
        window = self.window()
        with (
            patch.object(window, "request_face_gate", return_value=False) as gate,
            patch("glance.desktop.pages.PasswordDialog") as dialog,
        ):
            window.dashboard._set_password()
        gate.assert_called_once_with("Password")
        dialog.assert_not_called()
        window.close()


class StartupModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_startup_offers_models_before_onboarding(self) -> None:
        window = MainWindow()
        order: list[str] = []

        def offer_models(parent=None) -> bool:
            order.append("models")
            return True

        with patch.object(ModelsDialog, "ensure_models", side_effect=offer_models), \
                patch.object(MainWindow, "show_first_run_if_needed",
                             side_effect=lambda: order.append("onboarding")), \
                patch.object(MainWindow, "show_whats_new_if_needed",
                             side_effect=lambda: order.append("whats-new")), \
                patch.object(MainWindow, "offer_startup_if_needed",
                             side_effect=lambda: order.append("startup")):
            window.startup_sequence()

        self.assertEqual(order, ["models", "onboarding", "whats-new", "startup"])

    def test_whats_new_is_marked_after_being_shown(self) -> None:
        window = MainWindow()
        with (
            patch("glance.desktop.main.ui_state.should_show_whats_new", return_value=True),
            patch("glance.desktop.main.QMessageBox.information") as information,
            patch("glance.desktop.main.ui_state.mark_whats_new_shown") as mark,
        ):
            window.show_whats_new_if_needed()
        information.assert_called_once()
        mark.assert_called_once_with(desktop_main.__version__)
        window.close()

    def test_dashboard_distinguishes_manual_pause_from_unexpected_agent_exit(self) -> None:
        window = MainWindow()
        common = {
            "task": {"present": True, "enabled": False},
            "running": False,
            "credential": {"present": True, "account": "PC\\alex"},
            "identities": [{"name": "Alex", "samples": 8}],
            "unlock": "never",
        }
        window.dashboard._apply_snapshot({**common, "paused_until_wake": True})
        self.assertEqual(window.dashboard.status_title.text(), "Paused until wake")

        window.dashboard._apply_snapshot({**common, "paused_until_wake": False})
        self.assertEqual(window.dashboard.status_title.text(), "Protection stopped")
        self.assertIn("unexpectedly", window.dashboard.status_detail.text().lower())
        window.close()

    def test_startup_prompt_later_is_saved_and_not_repeated(self) -> None:
        window = MainWindow()

        class FakeMessageBox:
            AcceptRole = 0
            RejectRole = 1
            Information = 2

            def __init__(self, *_args):
                self.buttons = []
                self.choice = None

            def setWindowTitle(self, _title):
                pass

            def setText(self, _text):
                pass

            def addButton(self, text, role):
                button = (text, role)
                self.buttons.append(button)
                if text == "Later":
                    self.choice = button
                return button

            def exec(self):
                pass

            def clickedButton(self):
                return self.choice

        with (
            patch("glance.desktop.main.backend.task_state", return_value={"present": True, "enabled": False}),
            patch("glance.desktop.main.ui_state.should_offer_startup_prompt", return_value=True),
            patch("glance.desktop.main.ui_state.dismiss_startup_prompt") as dismiss,
            patch("glance.desktop.main.QMessageBox", FakeMessageBox),
            patch("glance.desktop.main.backend.set_autostart") as set_autostart,
        ):
            window.offer_startup_if_needed()

        dismiss.assert_called_once_with()
        set_autostart.assert_not_called()
        window.close()

    def test_ensure_models_is_silent_when_the_models_are_installed(self) -> None:
        with patch.object(backend, "models_ready", return_value=True), \
                patch.object(ModelsDialog, "exec") as shown:
            self.assertTrue(ModelsDialog.ensure_models())
        shown.assert_not_called()

    def test_declining_the_download_reports_models_are_missing(self) -> None:
        with patch.object(backend, "models_ready", return_value=False), \
                patch.object(ModelsDialog, "exec") as shown:
            self.assertFalse(ModelsDialog.ensure_models())
        shown.assert_called_once()


class EnrollmentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def make_page(self) -> EnrollPage:
        return EnrollPage(SimpleNamespace())

    def test_enrollment_animation_precedes_name_label_and_input(self) -> None:
        page = self.make_page()
        form_layout = page.name_edit.parentWidget().layout()
        name_title = next(
            widget for widget in page.name_edit.parentWidget().findChildren(QLabel)
            if widget.objectName() == "h2" and widget.text() == "Name"
        )
        self.assertLess(form_layout.indexOf(page.animation), form_layout.indexOf(name_title))
        self.assertLess(form_layout.indexOf(page.animation), form_layout.indexOf(page.name_edit))

    def test_identity_capture_card_uses_active_theme_selectors(self) -> None:
        from glance.desktop.capture_widgets import IdentityCaptureCard

        card = IdentityCaptureCard("Sampath", 12)
        self.assertEqual(card.objectName(), "identityCaptureCard")
        self.assertEqual(card.name.objectName(), "identityName")
        self.assertEqual(card.detail.objectName(), "identityDetail")
        self.assertNotIn("#22292f", card.styleSheet())
        for palette in (theme.DARK, theme.LIGHT):
            stylesheet = theme.build_stylesheet(palette)
            for selector in (
                "QFrame#identityCaptureCard",
                "QLabel#identityName",
                "QLabel#identityDetail",
            ):
                self.assertIn(selector, stylesheet)

    def test_capture_is_disabled_until_camera_ready(self) -> None:
        page = self.make_page()
        self.assertFalse(page.capture_button.isEnabled())

    def test_stale_failed_capture_does_not_change_enrollment(self) -> None:
        page = self.make_page()
        page._camera_ready = True
        page._capture_pending = True
        page.capture_button.setEnabled(False)
        page.on_capture(SimpleNamespace(embedding=None, ok=False, message="No face in view"))
        self.assertEqual(len(page._samples), 0)
        self.assertTrue(page.capture_button.isEnabled())
        self.assertEqual(page.quality.text(), "No samples yet")

    def test_eighteenth_good_sample_saves_session_automatically(self) -> None:
        page = self.make_page()
        page.name_edit.setText("Taylor")
        page._session_name = "Taylor"
        page._auto_active = True
        page.window.camera = SimpleNamespace(reload_store=lambda: None)
        page.window.on_identities_changed = lambda name: None
        base = np.zeros(128, dtype=np.float32)
        samples = []
        for index in range(1, 19):
            embedding = base.copy()
            embedding[0] = 1.0
            embedding[index] = .15
            embedding /= np.linalg.norm(embedding)
            samples.append(embedding)
        poses = ([(0, .5)] * 6 + [(-.2, .5)] * 3 + [( .2, .5)] * 3
                 + [(0, .35)] * 3 + [(0, .65)] * 3)

        with (
            patch("glance.desktop.pages.IdentityStore") as store_class,
            patch("glance.desktop.pages.backend.identity_summary", return_value=[]),
        ):
            for embedding, pose in zip(samples, poses):
                page.on_capture(SimpleNamespace(
                    embedding=embedding, ok=True, pose=pose, message="Good sample",
                ))

        self.assertEqual(store_class.return_value.add_sample.call_count, 18)
        store_class.return_value.save.assert_called_once()
        self.assertEqual(page.animation.state, "success")
        self.assertFalse(page._auto_active)
        self.assertIn("Saved 18 samples", page.quality.text())

    def test_enrollment_requires_a_stored_password_before_capture(self) -> None:
        visited: list[int] = []
        window = SimpleNamespace(
            _windows_password_stored=lambda: False,
            _navigate=lambda index: visited.append(index),
        )
        page = EnrollPage(window)
        page.name_edit.setText("Taylor")
        page._camera_ready = True
        with patch("glance.desktop.pages.QMessageBox.information") as inform:
            page._toggle_capture()
        self.assertFalse(page._auto_active)
        self.assertEqual(visited, [2])
        inform.assert_called_once()
        self.assertIn("password", page.quality.text().lower())

    def test_page_handoff_cancels_camera_stop_but_leaving_pages_stops_it(self) -> None:
        class FakeWorker:
            running = True
            stops = 0

            def isRunning(self):
                return self.running

            def stop(self):
                self.stops += 1

            def wait(self, timeout):
                self.running = False
                return True

            def start(self, *args):
                self.running = True

        window = MainWindow()
        worker = FakeWorker()
        window.camera = worker
        window._owners.add("dashboard")

        window.release_camera("dashboard")
        window.acquire_camera("enroll")
        QTest.qWait(350)
        self.assertEqual(worker.stops, 0, "page handoff must cancel the pending stop")

        window.release_camera("enroll")
        QTest.qWait(350)
        self.assertEqual(worker.stops, 1, "camera should stop after all owners leave")


class PasswordSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_rejected_password_is_never_stored_without_verification(self) -> None:
        dialog = PasswordDialog(None, "MicrosoftAccount\\person@example.com")
        dialog.password.setText("wrong-password")
        with (
            patch("glance.desktop.pages.QMessageBox.question", return_value=QMessageBox.Yes),
            patch.object(dialog, "_run") as run,
        ):
            dialog._finished(
                False, "the password was rejected by Windows",
                "MicrosoftAccount\\person@example.com", "wrong-password",
            )

        run.assert_not_called()
        self.assertEqual(dialog.password.text(), "")
        self.assertIn("not stored", dialog.status.text().lower())

    def test_verified_password_does_not_claim_unlock_is_enabled(self) -> None:
        dialog = PasswordDialog(None, "MicrosoftAccount\\person@example.com")
        dialog.password.setText("secret")
        dialog._finished(True, "stored credential for account", "account", "secret")
        self.assertEqual(dialog.password.text(), "")
        self.assertIn("enable windows unlock", dialog.status.text().lower())
        self.assertNotIn("ready", dialog.status.text().lower())

    def test_password_dialog_calls_current_two_argument_backend_contract(self) -> None:
        class FakeTask:
            def __init__(self, function, _parent=None):
                self.function = function
                self.done = SimpleNamespace(connect=lambda _callback: None)

            def start(self):
                pass

        dialog = PasswordDialog(None, "account")
        with patch("glance.desktop.pages.Task", FakeTask), patch(
            "glance.desktop.pages.backend.store_password", autospec=True, return_value=(True, "stored")
        ) as store_password:
            dialog._run("account", "secret")
            result = dialog._task.function()

        self.assertEqual(result, (True, "stored"))
        store_password.assert_called_once_with("account", "secret")


if __name__ == "__main__":
    unittest.main(verbosity=2)
