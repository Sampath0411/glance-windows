"""Repeatable, data-safe latency probe for Glance's desktop UI.

Run from the repository root with:
    .venv/Scripts/python.exe tools/diagnostics/profile_desktop.py --runs 3 --json

External Windows state and camera/model boundaries are stubbed. The current local
settings are read by MainWindow, but no settings, face data, or credentials are written.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QObject, qVersion
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from glance import __version__
from glance.desktop import backend
from glance.desktop.main import MainWindow
from glance.desktop.theme import resolve_theme


class _PaintProbe(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.painted_at: float | None = None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Paint and self.painted_at is None:
            self.painted_at = time.perf_counter()
        return False


def _wait_for(predicate, app: QApplication, timeout: float = 5.0) -> bool:
    deadline = time.perf_counter() + timeout
    while not predicate() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()
    return bool(predicate())


def _one_run(app: QApplication) -> dict[str, float]:
    empty = []
    state = {"present": False, "enabled": False}
    credential = {"present": False, "account": ""}
    patches = (
        patch.object(backend, "agent_running", return_value=False),
        patch.object(backend, "task_state", return_value=state),
        patch.object(backend, "credential_status", return_value=credential),
        patch.object(backend, "identity_summary", return_value=empty),
        patch.object(backend, "last_unlock", return_value="never"),
        patch.object(backend, "window_autostart_enabled", return_value=False),
        patch.object(backend, "models_ready", return_value=True),
        patch.object(backend.protection_state, "is_paused_until_wake", return_value=False),
        patch.object(backend, "log_text", return_value=""),
    )
    for item in patches:
        item.start()

    window = None
    probe = _PaintProbe()
    try:
        started = time.perf_counter()
        window = MainWindow()
        constructed = time.perf_counter()
        window.installEventFilter(probe)
        window.show()
        if not _wait_for(lambda: probe.painted_at is not None, app):
            raise RuntimeError("main window did not paint within 5 seconds")
        first_paint = probe.painted_at

        dashboard_started = time.perf_counter()
        window.dashboard.refresh()
        task = window.dashboard._refresh_task
        if task is not None and not _wait_for(task.isFinished, app):
            raise RuntimeError("dashboard snapshot did not finish within 5 seconds")
        dashboard_finished = time.perf_counter()

        nav_started = time.perf_counter()
        window._navigate(5)
        app.processEvents()
        navigation_finished = time.perf_counter()

        preview = window.camera_page.preview
        preview.resize(420, 240)
        preview.set_frame(
            QImage(640, 480, QImage.Format.Format_RGB32), [], "", ""
        )
        paint_started = time.perf_counter()
        preview.render(preview.grab())
        preview_paint_finished = time.perf_counter()

        return {
            "window_construct_ms": (constructed - started) * 1000,
            "first_paint_ms": (first_paint - started) * 1000,
            "dashboard_snapshot_ms": (dashboard_finished - dashboard_started) * 1000,
            "page_navigation_ms": (navigation_finished - nav_started) * 1000,
            "preview_paint_ms": (preview_paint_finished - paint_started) * 1000,
        }
    finally:
        if window is not None:
            window.close()
            window.camera.stop()
            window.camera.wait(1000)
            window.deleteLater()
            app.processEvents()
        for item in reversed(patches):
            item.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 20:
        parser.error("--runs must be between 1 and 20")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("Glance")
    app.setApplicationName("Glance UI Profile")
    app.setStyleSheet(resolve_theme("dark").stylesheet)

    runs = [_one_run(app) for _ in range(args.runs)]
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "qt": qVersion(),
        "runs": runs,
        "median_ms": {
            key: statistics.median(run[key] for run in runs)
            for key in runs[0]
        },
        "notes": "External state, models, camera, and logs stubbed; local settings are read-only.",
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Glance {report['version']} UI profile · Python {report['python']} · Qt {report['qt']}")
        for key, value in report["median_ms"].items():
            print(f"{key:24s} {value:8.2f} ms median")
        print(report["notes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
