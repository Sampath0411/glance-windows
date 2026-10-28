"""Persistent native navigation for Glance's six settings screens."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
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
    """Paint one 20pt line icon at 2x so it stays crisp on HiDPI screens."""
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    pen = QPen(QColor(color), 1.4)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if index == 0:  # General: cog
        painter.drawEllipse(6.0, 6.0, 8.0, 8.0)
        painter.drawEllipse(8.6, 8.6, 2.8, 2.8)
        for step in range(8):
            import math

            angle = math.radians(step * 45)
            x1 = 10 + 5.6 * math.cos(angle)
            y1 = 10 + 5.6 * math.sin(angle)
            x2 = 10 + 8.2 * math.cos(angle)
            y2 = 10 + 8.2 * math.sin(angle)
            painter.drawLine(int(x1 * 10), int(y1 * 10), int(x2 * 10), int(y2 * 10))
    elif index == 1:  # Your Face
        painter.drawRoundedRect(3.2, 2.2, 13.6, 15.6, 5.2, 5.2)
        painter.drawPoint(7.4, 7.8)
        painter.drawPoint(12.6, 7.8)
        painter.drawArc(6.6, 9.6, 6.8, 4.6, 200 * 16, 140 * 16)
    elif index == 2:  # Password: key
        painter.drawEllipse(3.0, 2.8, 6.6, 6.6)
        painter.drawLine(8.4, 8.2, 16.2, 16.0)
        painter.drawLine(12.4, 12.2, 14.6, 10.0)
        painter.drawLine(15.0, 14.8, 17.0, 12.8)
    elif index == 3:  # Camera
        painter.drawRoundedRect(2.2, 5.4, 15.6, 11.2, 2.6, 2.6)
        painter.drawEllipse(7.0, 8.0, 6.0, 6.0)
        painter.drawLine(7.2, 3.0, 12.8, 3.0)
    elif index == 4:  # Recognition: sliders
        for y, knob in ((5.2, 7.2), (10.0, 13.2), (14.8, 9.0)):
            painter.drawLine(3.0 * 10, int(y * 10), 17.0 * 10, int(y * 10))
            painter.drawEllipse(knob - 1.9, y - 1.9, 3.8, 3.8)
    else:  # About: info
        painter.drawEllipse(2.2, 2.2, 15.6, 15.6)
        painter.drawPoint(10.0, 6.4)
        painter.drawLine(10.0 * 10, 9.0 * 10, 10.0 * 10, 14.0 * 10)
    painter.end()
    return QIcon(pixmap)


class NavButton(QPushButton):
    """Sidebar row whose glyph brightens with the selected state."""

    def __init__(self, text: str, index: int, parent=None):
        super().__init__(text, parent)
        self._index = index
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setIconSize(QSize(19, 19))
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
