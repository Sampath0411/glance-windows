"""Render native pages with isolated sample state; no camera or machine operations."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtWidgets import QApplication
from glance.desktop import backend
from glance.desktop.main import MainWindow
from glance.desktop.pages import Dashboard, PasswordPage, EnrollPage
from glance.desktop.theme import resolve_theme

app = QApplication([])
app.setStyleSheet(resolve_theme("dark").stylesheet)
out = Path(__file__).resolve().parents[2] / "build" / "navy-ui-preview"
out.mkdir(parents=True, exist_ok=True)
with patch.object(Dashboard, "refresh"), patch.object(PasswordPage, "_load_status"), patch.object(EnrollPage, "showEvent"), patch.object(backend, "agent_running", return_value=False), patch.object(backend, "identity_summary", return_value=[]):
    window = MainWindow()
    window.show()
    window.dashboard._apply_snapshot({"task": {"present": True, "enabled": True}, "running": True, "credential": {"present": True, "account": "Sample account"}, "identities": [], "unlock": "never"})
    for i in range(6):
        window._navigate(i)
        app.processEvents()
        window.grab().save(str(out / f"page-{i}.png"))
        print(i, window.size().width(), window.size().height())
    window.close()
