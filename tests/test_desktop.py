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
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import glance.agent.glance_agent as glance_agent
import glance.desktop.backend as backend
import glance.desktop.theme as theme
from glance.desktop.onboarding import FirstRunDialog
from glance.desktop.models_setup import ModelsDialog
from glance.desktop.settings_widgets import FloatingTabBar, SettingsGroup, SettingsRow
from glance.desktop.backend import Settings
from glance.desktop.camera import CameraWorker
from glance.desktop.pages import EnrollPage, PasswordDialog, SettingsPage
from glance.desktop.main import MainWindow


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
            ["Overview", "Your face", "Windows sign-in", "Camera", "Recognition", "About"],
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

    def test_dashboard_routes_uninstalled_agent_to_in_app_setup(self) -> None:
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

    def _main_identity(self, config_bytes: bytes, cli_identity: str) -> str | None:
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
            return seen[0].identity

    def test_settings_identity_overrides_stale_task_argument(self) -> None:
        content = json.dumps({"identity": "Anu"}).encode("utf-8")
        self.assertEqual(self._main_identity(content, "Sampath"), "Anu")

    def test_empty_config_identity_does_not_fall_back_to_task_argument(self) -> None:
        content = json.dumps({"identity": ""}).encode("utf-8")
        self.assertIsNone(self._main_identity(content, "Sampath"))

    def test_corrupt_existing_config_does_not_fall_back_to_task_argument(self) -> None:
        self.assertIsNone(self._main_identity(b"{broken json", "Sampath"))

    def test_non_object_config_does_not_fall_back_to_task_argument(self) -> None:
        self.assertIsNone(self._main_identity(b"[]", "Sampath"))

    def test_agent_config_accepts_windows_utf8_bom(self) -> None:
        content = json.dumps({"identity": "Anu"}).encode("utf-8-sig")
        self.assertEqual(self._main_identity(content, "Sampath"), "Anu")

    def test_only_selected_face_can_write_a_grant(self) -> None:
        import numpy as np

        class FaceCamera:
            def __init__(self, stop: threading.Event, stop_after: int) -> None:
                self.stop = stop
                self.stop_after = stop_after
                self.reads = 0

            def isOpened(self) -> bool:
                return True

            def read(self):
                self.reads += 1
                if self.reads >= self.stop_after:
                    self.stop.set()
                return True, np.zeros((100, 100, 3), dtype=np.uint8)

            def release(self) -> None:
                pass

        class Pipeline:
            def __init__(self, selected_match: str, stop: threading.Event) -> None:
                identities = [SimpleNamespace(name="Sampath"), SimpleNamespace(name="Anu")]
                matched = next(i for i in identities if i.name == selected_match)
                self.store = SimpleNamespace(
                    identities=identities,
                    best_match=lambda embedding, threshold: (matched, 0.9, 0.9),
                )
                self.selected_match = selected_match
                self.camera = FaceCamera(stop, 1 if selected_match == "Sampath" else 4)

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

        for selected_identity in ("Sampath", "Anu"):
            for face_name in ("Sampath", "Anu"):
                with self.subTest(selected=selected_identity, face=face_name):
                    stop = threading.Event()
                    pipeline = Pipeline(face_name, stop)

                    def write_grant():
                        stop.set()
                        return "grant"

                    with (
                        patch.object(glance_agent, "GlancePipeline", return_value=pipeline),
                        patch.object(glance_agent.lv, "LivenessEvaluator", Evaluator),
                        patch.object(glance_agent.lv, "extract_glare", return_value=None),
                        patch.object(glance_agent.lv, "detect_device_rectangle", return_value=None),
                        patch.object(glance_agent.lv, "readings", return_value={}),
                        patch.object(glance_agent.grant_mod, "write_grant", side_effect=write_grant) as grant_writer,
                    ):
                        result = glance_agent.Verifier(
                            identity=selected_identity, require_stable=1, timeout=2
                        ).run(stop)

                    if face_name == selected_identity:
                        self.assertEqual(result, "granted")
                        grant_writer.assert_called_once()
                    else:
                        self.assertNotEqual(result, "granted")
                        grant_writer.assert_not_called()

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

    def make_page(self, selected: str) -> SettingsPage:
        window = SimpleNamespace(settings=Settings(identity=selected))
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

    def test_who_may_unlock_never_offers_any_enrolled(self) -> None:
        page = self.make_page("Sampath")
        self.assertEqual(page.identity.currentData(), "Sampath")
        self.assertEqual(page.identity.findData(""), -1)

    def test_save_is_disabled_when_no_person_is_selected(self) -> None:
        page = self.make_page("")
        self.assertFalse(page.save.isEnabled())

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
                             side_effect=lambda: order.append("onboarding")):
            window.startup_sequence()

        self.assertEqual(order, ["models", "onboarding"])

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

    def test_capture_is_disabled_until_camera_ready(self) -> None:
        page = self.make_page()
        self.assertFalse(page.capture_button.isEnabled())

    def test_failed_capture_does_not_increment_samples_and_reenables_button(self) -> None:
        page = self.make_page()
        page._camera_ready = True
        page._capture_pending = True
        page.capture_button.setEnabled(False)
        page.on_capture(SimpleNamespace(embedding=None, ok=False, message="No face in view"))
        self.assertEqual(len(page._samples), 0)
        self.assertTrue(page.capture_button.isEnabled())
        self.assertIn("No face", page.quality.text())

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
