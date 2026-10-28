"""Persistent native navigation for Glance's six settings screens."""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import MUTED, app_icon


def navigation_icon(index: int, color: str = MUTED) -> QIcon:
    """Paint one 20pt line icon at 2x so it stays crisp on HiDPI screens.

    Everything is drawn on a 20x20 grid in logical units, so a coordinate of 10 is the
    centre of the glyph. The pixmap is 2x with a device pixel ratio of 2, which gives
    Qt a 20pt icon and a 40px bitmap on high-DPI screens.
    """
    ratio = 2
    pixmap = QPixmap(20 * ratio, 20 * ratio)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    def fill_dot(x: float, y: float, radius: float = 0.85) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(x, y), radius, radius)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)

    if index == 0:  # General: cog
        points = []
        for step in range(8):
            base = math.radians(step * 45)
            for radius, offset in (
                (8.3, -9), (8.3, 9), (5.9, 14), (5.9, 31),
            ):
                angle = base + math.radians(offset)
                points.append(QPointF(10 + radius * math.cos(angle),
                                      10 + radius * math.sin(angle)))
        gear = QPainterPath(points[0])
        for point in points[1:]:
            gear.lineTo(point)
        gear.closeSubpath()
        painter.drawPath(gear)
        painter.drawEllipse(QPointF(10, 10), 2.2, 2.2)
    elif index == 1:  # Your Face
        painter.drawRoundedRect(QRectF(3.4, 2.6, 13.2, 14.8), 5.0, 5.0)
        fill_dot(7.4, 8.4, 0.75)
        fill_dot(12.6, 8.4, 0.75)
        painter.drawArc(QRectF(6.4, 9.2, 7.2, 5.0), 200 * 16, 140 * 16)
    elif index == 2:  # Password: key
        painter.drawEllipse(QPointF(6.4, 6.4), 3.4, 3.4)
        painter.drawLine(QPointF(8.9, 8.9), QPointF(16.6, 16.6))
        painter.drawLine(QPointF(12.5, 12.5), QPointF(14.3, 10.7))
        painter.drawLine(QPointF(14.9, 14.9), QPointF(16.6, 13.2))
    elif index == 3:  # Camera
        painter.drawRoundedRect(QRectF(2.6, 5.9, 14.8, 10.5), 2.8, 2.8)
        painter.drawEllipse(QPointF(10, 11.1), 3.0, 3.0)
        hump = QPainterPath(QPointF(7.1, 5.9))
        hump.lineTo(8.1, 3.9)
        hump.lineTo(11.9, 3.9)
        hump.lineTo(12.9, 5.9)
        painter.drawPath(hump)
    elif index == 4:  # Recognition: sliders
        for y, knob in ((5.4, 7.6), (10.0, 13.0), (14.6, 8.8)):
            painter.drawLine(QPointF(3.0, y), QPointF(knob - 2.4, y))
            painter.drawLine(QPointF(knob + 2.4, y), QPointF(17.0, y))
            painter.drawEllipse(QPointF(knob, y), 1.9, 1.9)
    else:  # About: info
        painter.drawEllipse(QPointF(10, 10), 7.6, 7.6)
        fill_dot(10, 6.5, 0.85)
        painter.drawLine(QPointF(10, 9.3), QPointF(10, 14.2))
    painter.end()
    return QIcon(pixmap)


class NavButton(QPushButton):
    """Sidebar row whose glyph brightens with the selected state."""

    def __init__(self, text: str, index: int, parent=None):
        super().__init__(text, parent)
        self._index = index
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setIconSize(QSize(20, 20))
        self.setMinimumHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(text)
        self.toggled.connect(self._sync_icon)
        self._sync_icon(False)

    def _sync_icon(self, checked: bool) -> None:
        colour = "#f6f9ff" if checked else "#94a4ba"
        self.setIcon(navigation_icon(self._index, colour))


class Sidebar(QFrame):
    """Grouped, translucent navigation rail with window controls."""

    indexChanged = Signal(int)

    def __init__(self, sections, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(238)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 20, 12, 15)
        layout.setSpacing(0)

        layout.addLayout(self._brand())
        layout.addSpacing(14)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[NavButton] = []
        index = 0
        for header, labels in sections:
            if header:
                caption = QLabel(header)
                caption.setObjectName("navSection")
                layout.addWidget(caption)
            for text in labels:
                button = NavButton(text, index)
                self._group.addButton(button, index)
                self.buttons.append(button)
                layout.addWidget(button)
                layout.addSpacing(2)
                index += 1
            layout.addSpacing(8)

        self._group.idClicked.connect(self.indexChanged.emit)
        self.set_current_index(0)

        layout.addStretch(1)
        layout.addWidget(self._status_pill())

    # -- chrome -----------------------------------------------------------
    def _brand(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(5, 0, 4, 0)
        row.setSpacing(9)

        glyph = QLabel()
        bitmap = app_icon().pixmap(44, 44)
        bitmap.setDevicePixelRatio(2)
        glyph.setPixmap(bitmap)
        glyph.setFixedSize(22, 22)
        row.addWidget(glyph)

        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel("Glance")
        name.setObjectName("brand")
        meta = QLabel("for Windows")
        meta.setObjectName("brandMeta")
        text.addWidget(name)
        text.addWidget(meta)
        row.addLayout(text)
        row.addStretch(1)
        return row

    def _status_pill(self) -> QFrame:
        from .theme import Dot

        pill = QFrame()
        pill.setObjectName("statusPill")
        pill.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(pill)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.status_dot = Dot()
        self.status_label = QLabel("checking...")
        self.status_label.setObjectName("statusText")
        row.addWidget(self.status_dot)
        row.addWidget(self.status_label, 1)
        return pill

    # -- api --------------------------------------------------------------
    def count(self) -> int:
        return len(self.buttons)

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def set_status(self, text: str, color: str | None = None) -> None:
        self.status_label.setText(text)
        if color:
            self.status_dot.set_color(color)


class WindowButton(QAbstractButton):
    """One minimal caption glyph: minimise, maximise/restore, or close."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._maximised = False
        self.setFixedSize(30, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip({"min": "Minimise", "max": "Maximise", "close": "Close"}[kind])
        self.setAccessibleName(self.toolTip())

    def set_maximised(self, value: bool) -> None:
        self._maximised = value
        self.setToolTip("Restore" if value else "Maximise")
        self.setAccessibleName(self.toolTip())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hovered = self.underMouse()
        if hovered:
            fill = QColor("#e0453c") if self.kind == "close" else QColor(255, 255, 255, 26)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 8, 8)

        colour = QColor(255, 255, 255, 235) if hovered else QColor(226, 236, 248, 170)
        pen = QPen(colour, 1.3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if self.kind == "min":
            painter.drawLine(QPointF(10, 13), QPointF(20, 13))
        elif self.kind == "max":
            if self._maximised:
                painter.drawRoundedRect(QRectF(9, 11, 9, 8), 1.5, 1.5)
                painter.drawRoundedRect(QRectF(12, 8.5, 8, 8), 1.5, 1.5)
            else:
                painter.drawRoundedRect(QRectF(10, 9, 10, 10), 1.8, 1.8)
        else:
            painter.drawLine(QPointF(10, 9), QPointF(20, 19))
            painter.drawLine(QPointF(20, 9), QPointF(10, 19))


class WindowControls(QWidget):
    """Glass caption controls that float in the top-right of the window."""

    closeRequested = Signal()
    minimizeRequested = Signal()
    zoomRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.minimise = WindowButton("min")
        self.zoom = WindowButton("max")
        self.close_button = WindowButton("close")
        self.minimise.clicked.connect(lambda _=False: self.minimizeRequested.emit())
        self.zoom.clicked.connect(lambda _=False: self.zoomRequested.emit())
        self.close_button.clicked.connect(lambda _=False: self.closeRequested.emit())
        for button in (self.minimise, self.zoom, self.close_button):
            layout.addWidget(button)
        self.setFixedSize(3 * 30 + 2 * 2, 26)

    def set_maximised(self, value: bool) -> None:
        self.zoom.set_maximised(value)


def style_native_window(window):
    """Request dark immersive chrome and keep native Windows behaviour."""
    import ctypes
    from ctypes import wintypes

    try:
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        for attribute, value in ((20, 1), (33, 2)):
            number = ctypes.c_int(value)
            set_attribute(
                int(window.winId()), attribute, ctypes.byref(number), ctypes.sizeof(number)
            )
    except (AttributeError, OSError):
        pass  # Older Windows versions retain their standard native frame.
