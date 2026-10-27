"""Persistent native navigation for Glance's six settings screens."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QPushButton, QVBoxLayout

from .theme import MUTED


def navigation_icon(index: int) -> QIcon:
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(MUTED), 1.3))
    if index == 0:
        for x, y in ((3, 3), (11, 3), (3, 11), (11, 11)):
            painter.drawRoundedRect(x, y, 6, 6, 1, 1)
    elif index == 1:
        painter.drawRoundedRect(3, 2, 14, 16, 5, 5)
        painter.drawPoint(7, 8)
        painter.drawPoint(13, 8)
        painter.drawArc(7, 10, 6, 4, 180 * 16, 180 * 16)
    elif index == 2:
        painter.drawRoundedRect(4, 8, 12, 10, 2, 2)
        painter.drawArc(6, 1, 8, 13, 0, 180 * 16)
        painter.drawLine(10, 12, 10, 15)
    elif index == 3:
        painter.drawRoundedRect(2, 5, 16, 12, 2, 2)
        painter.drawEllipse(7, 8, 6, 6)
        painter.drawLine(7, 3, 13, 3)
    elif index == 4:
        for y, x in ((5, 7), (10, 13), (15, 9)):
            painter.drawLine(3, y, 17, y)
            painter.drawEllipse(x - 2, y - 2, 4, 4)
    else:
        painter.drawEllipse(2, 2, 16, 16)
        painter.drawLine(10, 9, 10, 14)
        painter.drawPoint(10, 6)
    painter.end()
    return QIcon(pixmap)


class Sidebar(QFrame):
    indexChanged = Signal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(205)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 28, 14, 24)
        layout.setSpacing(6)
        brand = QLabel("Glance")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(24)
        self._group = QButtonGroup(self)
        self.buttons = []
        for index, text in enumerate(labels):
            button = QPushButton(text)
            button.setObjectName("nav")
            button.setIcon(navigation_icon(index))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.setAccessibleName(text)
            self._group.addButton(button, index)
            self.buttons.append(button)
            layout.addWidget(button)
        self._group.idClicked.connect(self.indexChanged.emit)
        self.set_current_index(0)
        layout.addStretch()
        footer = QLabel("ON THIS DEVICE\n\nGlance for Windows")
        footer.setObjectName("sidebarFooter")
        layout.addWidget(footer)

    def count(self):
        return len(self.buttons)

    def set_current_index(self, index):
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)


def style_native_window(window):
    """Keep Windows snap/resizing and request native rounded dark chrome."""
    import ctypes
    from ctypes import wintypes

    try:
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        for attribute, value in ((20, 1), (33, 2), (35, 0x20140E), (36, 0xF3EDE6)):
            number = ctypes.c_int(value)
            set_attribute(int(window.winId()), attribute, ctypes.byref(number), ctypes.sizeof(number))
    except (AttributeError, OSError):
        pass  # Older Windows versions retain their standard native frame.
