"""Compact controls shared by the glass settings pages."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, MUTED, TEXT


class SettingsGroup(QFrame):
    """Rounded, translucent group for related settings rows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsGroup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(0)

    def add_row(self, row: QWidget) -> None:
        if self.column.count():
            self.column.addWidget(SettingsDivider())
        self.column.addWidget(row)


class SettingsDivider(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsDivider")
        self.setFixedHeight(1)


class SettingsRow(QWidget):
    """Title/subtitle row with a trailing native Qt control."""

    def __init__(self, title: str, subtitle: str = "", trailing: QWidget | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 15, 16, 15)
        row.setSpacing(12)
        labels = QVBoxLayout()
        labels.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("rowTitle")
        labels.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        # Always in the layout so callers can fill in a status later.
        labels.addWidget(self.subtitle)
        row.addLayout(labels, 1)
        self.trailing_host = QWidget()
        self.trailing_layout = QHBoxLayout(self.trailing_host)
        self.trailing_layout.setContentsMargins(0, 0, 0, 0)
        self.trailing_layout.addStretch(1)
        row.addWidget(self.trailing_host)
        self.set_trailing(trailing)

    def set_trailing(self, widget: QWidget | None) -> None:
        while self.trailing_layout.count() > 1:
            item = self.trailing_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        if widget is not None:
            self.trailing_layout.insertWidget(0, widget)


class FloatingTabBar(QWidget):
    """Small checkable bottom navigation bar; pages are provided by caller."""

    indexChanged = Signal(int)

    def __init__(self, labels: tuple[str, ...], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("floatingTabBar")
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(2)
        self.buttons: list[QPushButton] = []
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setObjectName("tab")
            button.setCheckable(True)
            button.setAccessibleName(label)
            self._group.addButton(button, index)
            layout.addWidget(button, 1)
            self.buttons.append(button)
        self._group.idClicked.connect(self.indexChanged.emit)
        if self.buttons:
            self.buttons[0].setChecked(True)

    def count(self) -> int:
        return len(self.buttons)

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)


class Select(QComboBox):
    """Combo box with a chevron painted over the glass field.

    Styling ``::drop-down`` suppresses Qt's native arrow, which is drawn in a
    dark system colour and would disappear against the translucent field.
    """

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(216, 230, 248, 195), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        x = float(self.width()) - 19.0
        y = self.height() / 2.0 - 2.5
        painter.drawPolyline(
            QPolygonF([QPointF(x, y), QPointF(x + 4.0, y + 4.0), QPointF(x + 8.0, y)])
        )


class NumberField(QSpinBox):
    """Spin box whose stepper arrows stay visible on the dark field."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(216, 230, 248, 180), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx = float(self.width()) - 13.0
        mid = self.height() / 2.0
        painter.drawPolyline(
            QPolygonF(
                [QPointF(cx - 3.5, mid - 4.0), QPointF(cx, mid - 7.5), QPointF(cx + 3.5, mid - 4.0)]
            )
        )
        painter.drawPolyline(
            QPolygonF(
                [QPointF(cx - 3.5, mid + 4.0), QPointF(cx, mid + 7.5), QPointF(cx + 3.5, mid + 4.0)]
            )
        )


class CheckBox(QCheckBox):
    """Check box with a painted tick.

    The stylesheet draws the rounded box but Qt's native check mark is clipped
    away by the custom indicator, so the tick is drawn here instead.
    """

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.isChecked():
            return
        size = 17.0
        top = (self.height() - size) / 2.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(4.2, top + 8.9),
                    QPointF(7.3, top + 12.0),
                    QPointF(13.0, top + 5.3),
                ]
            )
        )


class ChoiceTile(QAbstractButton):
    """Large selectable tile with a blue outline, mirroring the reference UI."""

    def __init__(self, key: str, title: str, caption: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self._hover = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(title)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(1)
        self.title_label = QLabel(title)
        self.caption_label = QLabel(caption)
        for label in (self.title_label, self.caption_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.caption_label.setStyleSheet(f"font-size: 11px; color: {MUTED};")
        layout.addWidget(self.title_label)
        layout.addWidget(self.caption_label)
        self.toggled.connect(self._sync)
        self._sync(False)

    def _sync(self, checked: bool) -> None:
        self.title_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {'#ffffff' if checked else TEXT};"
        )
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = 14.0

        if self.isChecked():
            glow = QColor(ACCENT)
            glow.setAlpha(46)
            painter.setPen(QPen(glow, 5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
            fill = QColor(ACCENT)
            fill.setAlpha(30)
            painter.setBrush(fill)
            pen = QPen(QColor(ACCENT), 2)
        elif self._hover:
            painter.setBrush(QColor(255, 255, 255, 22))
            pen = QPen(QColor(255, 255, 255, 40), 1)
        else:
            painter.setBrush(QColor(255, 255, 255, 11))
            pen = QPen(QColor(255, 255, 255, 22), 1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, radius, radius)


class SegmentedTiles(QWidget):
    """Exclusive row of ChoiceTiles; emits the selected key."""

    valueChanged = Signal(str)

    def __init__(self, options, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.tiles: dict[str, ChoiceTile] = {}
        for index, (key, title, caption) in enumerate(options):
            tile = ChoiceTile(key, title, caption)
            self._group.addButton(tile, index)
            self.tiles[key] = tile
            layout.addWidget(tile, 1)
        self._group.idClicked.connect(self._clicked)

    def _clicked(self, index: int) -> None:
        tile = self._group.button(index)
        if isinstance(tile, ChoiceTile):
            self.valueChanged.emit(tile.key)

    def value(self) -> str | None:
        for key, tile in self.tiles.items():
            if tile.isChecked():
                return key
        return None

    def set_value(self, key: str | None) -> None:
        self._group.setExclusive(False)
        for name, tile in self.tiles.items():
            tile.setChecked(name == key)
        self._group.setExclusive(True)
