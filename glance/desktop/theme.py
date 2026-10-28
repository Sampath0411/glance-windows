"""Palette, glass stylesheet, painted backdrop and the shared widgets."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QFrame, QWidget

BG = "#0a0f16"
PANEL = "#111923"
PANEL_ALT = "#0c131c"
BORDER = "#233040"
TEXT = "#eef3fa"
MUTED = "#9aa9bd"
ACCENT = "#4c9dff"
WARN = "#e6b95c"
DANGER = "#ff6b6b"
BLUE = "#4c9dff"


@dataclass(frozen=True)
class Palette:
    background: str
    foreground: str
    muted: str
    faint: str
    panel: str
    card_top: str
    card_bottom: str
    surface: str
    surface_hover: str
    surface_pressed: str
    border: str
    border_strong: str
    field: str
    sidebar: str
    nav_selected: str
    accent: str
    accent_hi: str
    accent_text: str
    danger: str
    warn: str


DARK = Palette(
    background=BG,
    foreground=TEXT,
    muted="#9aa9bd",
    faint="rgba(170, 186, 206, 0.62)",
    panel="#101823",
    card_top="rgba(255, 255, 255, 0.085)",
    card_bottom="rgba(255, 255, 255, 0.032)",
    surface="rgba(255, 255, 255, 0.075)",
    surface_hover="rgba(255, 255, 255, 0.125)",
    surface_pressed="rgba(255, 255, 255, 0.055)",
    border="rgba(255, 255, 255, 0.10)",
    border_strong="rgba(255, 255, 255, 0.16)",
    field="rgba(255, 255, 255, 0.065)",
    sidebar="rgba(14, 22, 34, 0.52)",
    nav_selected="rgba(255, 255, 255, 0.09)",
    accent=ACCENT,
    accent_hi="#74b4ff",
    accent_text="#ffffff",
    danger=DANGER,
    warn=WARN,
)

LIGHT = Palette(
    background="#eef2f7",
    foreground="#0d1622",
    muted="#5a6778",
    faint="rgba(60, 76, 96, 0.60)",
    panel="#f7f9fc",
    card_top="rgba(255, 255, 255, 0.92)",
    card_bottom="rgba(248, 251, 255, 0.80)",
    surface="rgba(255, 255, 255, 0.80)",
    surface_hover="rgba(255, 255, 255, 0.98)",
    surface_pressed="rgba(236, 242, 249, 0.92)",
    border="rgba(12, 24, 38, 0.10)",
    border_strong="rgba(12, 24, 38, 0.18)",
    field="rgba(255, 255, 255, 0.88)",
    sidebar="rgba(243, 247, 252, 0.72)",
    nav_selected="rgba(12, 24, 38, 0.08)",
    accent="#2f7fe8",
    accent_hi="#5aa2f2",
    accent_text="#ffffff",
    danger="#c0392b",
    warn="#9a6b00",
)

STYLESHEET_TEMPLATE = Template(
    """
QWidget {
    color: $foreground;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
}
QMainWindow { background: transparent; }
QDialog, QMessageBox {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0d1723, stop:1 #091019);
    border: 1px solid $border;
}
QFrame#card, QFrame#settingsGroup {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $card_top, stop:1 $card_bottom);
    border: 1px solid $border;
    border-radius: 16px;
}
QFrame#sidebar {
    background: $sidebar;
    border: none;
    border-right: 1px solid $border;
    border-top-left-radius: 20px;
    border-bottom-left-radius: 20px;
}
QFrame#sidebar[framed="false"] {
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
}
QFrame#statusPill {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QLabel#statusText { color: $muted; font-size: 12px; font-weight: 500; }
QWidget#settingsRow { background: transparent; }
QFrame#settingsDivider { background: $border; border: none; }
QLabel { background: transparent; }
QLabel#h1 { font-size: 20px; font-weight: 600; }
QLabel#h2 { font-size: 13px; font-weight: 600; padding-top: 4px; }
QLabel#big { font-size: 18px; font-weight: 600; }
QLabel#rowTitle { font-size: 13px; font-weight: 500; }
QLabel#body { font-size: 13px; color: $foreground; }
QLabel#muted { color: $muted; font-size: 12px; }
QLabel#pageSub { color: $muted; font-size: 13px; }
QLabel#mono {
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 12px;
    color: $muted;
}
QLabel#brand { font-size: 15px; font-weight: 600; }
QLabel#brandMeta { font-size: 10px; font-weight: 500; color: $faint; }
QLabel#navSection {
    color: $faint;
    font-size: 12px;
    font-weight: 600;
    padding: 14px 12px 5px 12px;
}
QPushButton {
    background: $surface;
    color: $foreground;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 500;
}
QPushButton:hover { background: $surface_hover; }
QPushButton:pressed { background: $surface_pressed; }
QPushButton:disabled {
    color: $faint;
    background: transparent;
    border-color: $border;
}
QPushButton:flat {
    background: transparent;
    border: none;
    padding: 2px;
    color: $accent;
}
QPushButton:flat:hover { color: $accent_hi; }
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $accent_hi, stop:1 $accent);
    border: 1px solid rgba(255, 255, 255, 0.24);
    color: $accent_text;
    font-weight: 600;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #8ac2ff, stop:1 $accent_hi);
}
QPushButton#primary:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $accent, stop:1 #2d6fd0);
}
QPushButton#primary:disabled {
    background: rgba(76, 157, 255, 0.18);
    border-color: transparent;
    color: rgba(255, 255, 255, 0.42);
}
QPushButton#danger {
    background: transparent;
    color: $danger;
    border: 1px solid rgba(255, 107, 107, 0.28);
}
QPushButton#danger:hover { background: rgba(255, 107, 107, 0.10); }
QPushButton#nav {
    text-align: left;
    padding: 9px 12px;
    border: 1px solid transparent;
    border-radius: 10px;
    background: transparent;
    color: $muted;
    font-weight: 500;
}
QPushButton#nav:hover {
    background: rgba(255, 255, 255, 0.055);
    color: $foreground;
}
QPushButton#nav:checked {
    background: $nav_selected;
    border-color: $border;
    color: $foreground;
    font-weight: 600;
}
QPushButton#winClose, QPushButton#winMin, QPushButton#winZoom {
    border: none;
    border-radius: 6px;
    padding: 0;
}
QPushButton#winClose { background: #ff5f57; }
QPushButton#winClose:hover { background: #ff837d; }
QPushButton#winMin { background: #febc2e; }
QPushButton#winMin:hover { background: #ffd066; }
QPushButton#winZoom { background: #28c840; }
QPushButton#winZoom:hover { background: #5cdd70; }
QLineEdit, QSpinBox, QComboBox {
    background: $field;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 7px 10px;
    color: $foreground;
    selection-background-color: $accent;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: rgba(76, 157, 255, 0.75);
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: $panel;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 4px;
    selection-background-color: rgba(76, 157, 255, 0.30);
}
QSpinBox::up-button, QSpinBox::down-button {
    background: transparent;
    border: none;
    width: 18px;
}
QSpinBox::up-arrow, QSpinBox::down-arrow { image: none; width: 0; height: 0; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid $border_strong;
    background: $field;
}
QCheckBox::indicator:hover { border-color: rgba(76, 157, 255, 0.65); }
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $accent_hi, stop:1 $accent);
    border-color: rgba(255, 255, 255, 0.30);
}
QSlider::groove:horizontal {
    height: 5px;
    background: rgba(255, 255, 255, 0.13);
    border-radius: 2px;
}
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #ffffff;
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}
QProgressBar {
    background: rgba(255, 255, 255, 0.10);
    border: none;
    border-radius: 5px;
    height: 8px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $accent, stop:1 #7fd6ff);
    border-radius: 5px;
}
QPlainTextEdit {
    background: rgba(0, 0, 0, 0.26);
    border: 1px solid $border;
    border-radius: 12px;
    padding: 10px;
    color: #d7e3f2;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 12px;
}
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QAbstractScrollArea::corner { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.16);
    min-height: 32px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.28); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QToolTip {
    background: $panel;
    color: $foreground;
    border: 1px solid $border;
    padding: 6px 8px;
    border-radius: 8px;
}
QMenu {
    background: $panel;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: rgba(76, 157, 255, 0.24); }
QMenu::separator {
    height: 1px;
    margin: 5px 8px;
    background: $border;
}
"""
)


def build_stylesheet(palette: Palette) -> str:
    return STYLESHEET_TEMPLATE.substitute(
        background=palette.background,
        foreground=palette.foreground,
        muted=palette.muted,
        faint=palette.faint,
        panel=palette.panel,
        card_top=palette.card_top,
        card_bottom=palette.card_bottom,
        surface=palette.surface,
        surface_hover=palette.surface_hover,
        surface_pressed=palette.surface_pressed,
        border=palette.border,
        border_strong=palette.border_strong,
        field=palette.field,
        sidebar=palette.sidebar,
        nav_selected=palette.nav_selected,
        accent=palette.accent,
        accent_hi=palette.accent_hi,
        accent_text=palette.accent_text,
        danger=palette.danger,
        warn=palette.warn,
    )


STYLESHEET = build_stylesheet(DARK)


@dataclass(frozen=True)
class ResolvedTheme:
    """Static colors and stylesheet for one Windows appearance mode."""

    background: str
    foreground: str
    stylesheet: str


def resolve_theme(appearance: str) -> ResolvedTheme:
    """Resolve a light or dark glass theme; no runtime effects are involved."""
    palette = DARK if appearance.lower() != "light" else LIGHT
    return ResolvedTheme(palette.background, palette.foreground, build_stylesheet(palette))


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


# --------------------------------------------------------------------------- #
# painted backdrop
# --------------------------------------------------------------------------- #
def paint_backdrop(painter: QPainter, rect: QRectF, *, shade: float = 1.0) -> None:
    """Paint the liquid-glass field the translucent panels sit over.

    The field is built from layered radial gradients and a few diagonal light
    shards, so the window keeps its depth on any desktop and in screenshots.
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)

    base = QLinearGradient(rect.topLeft(), rect.bottomRight())
    base.setColorAt(0.0, QColor(9, 16, 25))
    base.setColorAt(0.45, QColor(12, 23, 36))
    base.setColorAt(0.72, QColor(8, 22, 33))
    base.setColorAt(1.0, QColor(6, 11, 18))
    painter.fillRect(rect, base)

    painter.save()
    painter.setClipRect(rect)

    shards = (
        (-0.30, 0.30, 8, QColor(120, 205, 255, 14)),
        (0.08, 0.20, 10, QColor(255, 255, 255, 9)),
        (0.46, 0.34, 6, QColor(90, 220, 215, 12)),
        (0.74, 0.26, 8, QColor(140, 170, 255, 11)),
    )
    for start, width, alpha, color in shards:
        x = rect.left() + rect.width() * start
        band_width = max(24.0, rect.width() * width)
        skew = rect.height() * 0.42
        polygon = QPolygonF(
            [
                QPointF(x, rect.top()),
                QPointF(x + band_width, rect.top()),
                QPointF(x + band_width - skew, rect.bottom()),
                QPointF(x - skew, rect.bottom()),
            ]
        )
        gradient = QLinearGradient(
            QPointF(x, rect.top()), QPointF(x + band_width, rect.bottom())
        )
        gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 0))
        gradient.setColorAt(0.35, QColor(color.red(), color.green(), color.blue(), alpha))
        gradient.setColorAt(0.70, QColor(color.red(), color.green(), color.blue(), alpha // 2))
        gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPolygon(polygon)

    blobs = (
        (0.10, -0.04, 0.72, QColor(38, 128, 235, int(96 * shade))),
        (0.94, 0.14, 0.60, QColor(32, 200, 188, int(62 * shade))),
        (0.72, 0.96, 0.72, QColor(86, 104, 246, int(78 * shade))),
        (0.00, 0.88, 0.56, QColor(18, 112, 214, int(62 * shade))),
        (0.44, 0.40, 0.42, QColor(20, 46, 78, int(70 * shade))),
    )
    for cx, cy, radius_ratio, color in blobs:
        center = QPointF(
            rect.left() + rect.width() * cx, rect.top() + rect.height() * cy
        )
        radius = max(rect.width(), rect.height()) * radius_ratio
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0.0, color)
        gradient.setColorAt(
            0.48,
            QColor(color.red(), color.green(), color.blue(), color.alpha() // 2),
        )
        gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

    sheen = QLinearGradient(
        QPointF(rect.left(), rect.top()),
        QPointF(rect.left(), rect.top() + rect.height() * 0.55),
    )
    sheen.setColorAt(0.0, QColor(255, 255, 255, 13))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(rect, sheen)

    vignette = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.78)
    vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
    vignette.setColorAt(0.62, QColor(0, 0, 0, 0))
    vignette.setColorAt(1.0, QColor(0, 0, 0, 96))
    painter.fillRect(rect, vignette)

    painter.restore()
    painter.restore()


def backdrop_pixmap(size, ratio: float = 1.0) -> QPixmap:
    """Render the backdrop once per window size for cheap repaints."""
    width = max(1, int(size.width() * ratio))
    height = max(1, int(size.height() * ratio))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    paint_backdrop(painter, QRectF(0, 0, width, height))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


# --------------------------------------------------------------------------- #
# widgets
# --------------------------------------------------------------------------- #
class Dot(QWidget):
    """A small status light with a soft halo."""

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
        halo = QColor(self._color)
        halo.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(0, 0, 12, 12))
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(3, 3, 6, 6))


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
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
        painter.setClipPath(path)

        if self._image is None:
            field = QLinearGradient(
                QPointF(0, 0), QPointF(self.width(), self.height())
            )
            field.setColorAt(0.0, QColor(16, 27, 41))
            field.setColorAt(1.0, QColor(9, 16, 25))
            painter.fillRect(self.rect(), field)
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "Camera off")
            painter.setClipping(False)
            painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
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
            painter.drawRoundedRect(rect, 12, 12)

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
            painter.drawText(
                self.rect().adjusted(0, 0, -16, -12),
                Qt.AlignRight | Qt.AlignBottom,
                self._hint,
            )

        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


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
