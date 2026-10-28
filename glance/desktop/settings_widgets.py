"""Compact controls shared by the Mac-inspired Windows settings pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SettingsGroup(QFrame):
    """Rounded, subtly filled group for related settings rows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsGroup")
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(12, 4, 12, 4)
        self.column.setSpacing(0)

    def add_row(self, row: QWidget) -> None:
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
        row.setContentsMargins(8, 16, 8, 16)
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
        if subtitle:
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
