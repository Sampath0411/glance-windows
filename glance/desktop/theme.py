"""Palette, stylesheet and the two or three painted widgets the app needs."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QWidget

BG = "#101a2b"
PANEL = "#162338"
PANEL_ALT = "#0e1727"
BORDER = "#26364e"
TEXT = "#e6edf3"
MUTED = "#8b98a8"
ACCENT = "#82b4ff"
WARN = "#e3b341"
DANGER = "#f85149"
BLUE = "#4c8dff"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI Variable Text", "Segoe UI";
    font-size: 13px;
}}
QFrame#card {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#sidebar {{
    background: {PANEL};
    border: none;
    border-right: 1px solid {BORDER};
}}
QFrame#settingsGroup {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 14px;
}}
QFrame#settingsDivider {{ background: {BORDER}; border: none; max-height: 1px; }}
QWidget#floatingTabBar {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 19px; }}
QPushButton#tab {{ background: transparent; border: none; color: {MUTED}; padding: 8px 7px; border-radius: 15px; }}
QPushButton#tab:hover {{ background: #151c26; color: {TEXT}; }}
QPushButton#tab:checked {{ background: #1b2431; color: {TEXT}; font-weight: 600; }}
QLabel#rowTitle {{ font-size: 13px; font-weight: 500; }}
QLabel#h1 {{ font-size: 21px; font-weight: 600; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; }}
QLabel#big {{ font-size: 19px; font-weight: 600; }}
QLabel#mono {{ font-family: Consolas, "Cascadia Mono"; font-size: 12px; color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#brand {{ font-size: 17px; font-weight: 700; letter-spacing: 1px; }}
QPushButton {{
    background: #1b2431;
    border: 1px solid #2a3543;
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background: #212c3a; }}
QPushButton:pressed {{ background: #182231; }}
QPushButton:disabled {{ color: #5b6675; border-color: #1d2634; }}
QPushButton#primary {{
    background: {ACCENT}; color: #06110a; border: none; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #46e58a; }}
QPushButton#primary:disabled {{ background: #23392c; color: #6d7f74; }}
QPushButton#danger {{ color: {DANGER}; border-color: #46262a; background: transparent; }}
QPushButton#danger:hover {{ background: #2a1719; }}
QPushButton#nav {{
    text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
    background: transparent; color: {MUTED};
}}
QPushButton#nav:hover {{ background: #151c26; color: {TEXT}; }}
QPushButton#nav:checked {{ background: #16241c; color: {ACCENT}; font-weight: 600; }}
QLineEdit, QSpinBox, QComboBox {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER};
    selection-background-color: #1c2a22;
}}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
}}
QPlainTextEdit {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 10px;
    font-family: Consolas, "Cascadia Mono"; font-size: 12px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #2a3543; background: {PANEL_ALT};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: #1b2431; color: {TEXT}; border: 1px solid #2a3543; }}
QMessageBox {{ background: {PANEL}; }}
QProgressBar {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 6px;
    text-align: center; height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
"""

STYLESHEET += f"""
QLabel, QCheckBox, QWidget#settingsRow {{ background: transparent; }}
QLabel#h1 {{ font-size: 27px; font-weight: 600; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; margin-top: 8px; }}
QLabel#big {{ font-size: 22px; font-weight: 600; }}
QLabel#brand {{ font-size: 25px; padding: 0px 12px; color: {TEXT}; }}
QLabel#sidebarFooter {{ font-size: 11px; color: {MUTED}; padding: 12px; }}
QFrame#sidebar {{ background: {PANEL_ALT}; border-right: 1px solid {BORDER}; }}
QPushButton#nav {{ padding: 10px 12px; border-radius: 6px; }}
QPushButton#nav:checked {{ background: #203859; color: {ACCENT}; }}
QPushButton#nav:hover {{ background: #1b2c45; }}
QPushButton {{ padding: 9px 16px; border-radius: 6px; background: #1c2d46; border-color: {BORDER}; }}
QPushButton:hover {{ background: #273e5d; }}
QPushButton#primary {{ background: {ACCENT}; color: #0c1930; }}
QPushButton#primary:hover {{ background: #a2c8ff; }}
QPushButton#primary:disabled {{ background: #2b3c55; color: #8493a8; }}
QFrame#card, QFrame#settingsGroup {{ border-radius: 9px; }}
QLineEdit, QSpinBox, QComboBox {{ padding: 9px; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #344660; min-height: 28px; border-radius: 4px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


@dataclass(frozen=True)
class ResolvedTheme:
    """Static colors and stylesheet for one Windows appearance mode."""

    background: str
    foreground: str
    stylesheet: str


def resolve_theme(appearance: str) -> ResolvedTheme:
    """Resolve a low-cost light or dark theme without runtime blur effects."""
    if appearance.lower() != "light":
        return ResolvedTheme(BG, TEXT, STYLESHEET)

    replacements = {
        BG: "#f5f5f7",
        PANEL: "#ffffff",
        PANEL_ALT: "#f0f0f2",
        BORDER: "#d2d2d7",
        TEXT: "#1d1d1f",
        MUTED: "#6e6e73",
        "#1b2431": "#e8e8ed",
        "#2a3543": "#d2d2d7",
        "#212c3a": "#dedee3",
        "#182231": "#ebebef",
        "#5b6675": "#99999f",
        "#1d2634": "#dedee3",
        "#46e58a": "#2ac769",
        "#23392c": "#cfe8d6",
        "#6d7f74": "#52735c",
        "#46262a": "#f1d0d0",
        "#2a1719": "#f8e7e7",
        "#151c26": "#e9e9ed",
        "#16241c": "#e0f2e6",
        "#0e141c": "#ffffff",
        "#1c2a22": "#dff0e5",
        "#080b0f": "#e5e5e7",
        "#151c26": "#e9e9ed",
        "#1b2431": "#e8e8ed",
    }
    stylesheet = STYLESHEET
    for dark, light in replacements.items():
        stylesheet = stylesheet.replace(dark, light)
    return ResolvedTheme("#f5f5f7", "#1d1d1f", stylesheet)


def windows_appearance() -> str:
    """Read the Windows app appearance once; fall back to Qt's system palette."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if value else "dark"
    except (ImportError, OSError):
        return "light" if QApplication.palette().window().color().lightness() > 128 else "dark"


class Dot(QWidget):
    """A 10px status light."""

    def __init__(self, color: str = MUTED, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(12, 12)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(1, 1, 10, 10))


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")


class Preview(QWidget):
    """Camera frame with a face box, drawn by hand so it scales cleanly."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self._faces: list = []
        self._label = ""
        self._hint = ""
        self.setMinimumSize(420, 240)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def set_frame(self, image, faces, label: str, hint: str) -> None:
        self._image = image
        self._faces = faces
        self._label = label
        self._hint = hint
        self.update()

    def clear(self) -> None:
        self._image = None
        self._faces = []
        self._label = ""
        self._hint = ""
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#080b0f"))

        if self._image is None:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "Camera off")
            return

        target = self.rect()
        scaled = self._image.scaled(
            target.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        offset_x = (scaled.width() - target.width()) // 2
        offset_y = (scaled.height() - target.height()) // 2
        painter.drawImage(-offset_x, -offset_y, scaled)

        scale = scaled.width() / self._image.width()
        pen = QPen(QColor(ACCENT))
        pen.setWidth(2)
        painter.setPen(pen)
        for (x1, y1, x2, y2) in self._faces:
            rect = QRectF(
                x1 * scale - offset_x,
                y1 * scale - offset_y,
                (x2 - x1) * scale,
                (y2 - y1) * scale,
            )
            painter.drawRoundedRect(rect, 10, 10)

        if self._label:
            painter.setPen(QColor(ACCENT))
            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(16, 28, self._label.upper())

        if self._hint:
            painter.setPen(QColor(WARN))
            font = painter.font()
            font.setPointSize(10)
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(self.rect().adjusted(0, 0, -16, -12),
                             Qt.AlignRight | Qt.AlignBottom, self._hint)


_APP_ICON: QIcon | None = None


def _resource_root() -> Path:
    """Locate immutable assets in source runs and packaged standalone builds."""
    if getattr(sys, "frozen", False) or os.environ.get("GLANCE_PACKAGED") == "1":
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def app_icon() -> QIcon:
    """Return the approved Glance artwork, exactly as supplied."""
    global _APP_ICON
    if _APP_ICON is not None:
        return _APP_ICON

    icon = QIcon(str(_resource_root() / "assets" / "GlanceIcon.png"))
    if icon.isNull():
        # Keep the window usable if a damaged install omitted the artwork.
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(BG))
        painter.setPen(QPen(QColor(ACCENT), 2))
        painter.drawRoundedRect(QRectF(3, 3, 58, 58), 16, 16)
        painter.end()
        icon = QIcon(pixmap)

    _APP_ICON = icon
    return icon
