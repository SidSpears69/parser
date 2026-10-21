"""Shared Qt form helpers and readable controls."""

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QLabel, QLineEdit, QSpinBox, QTimeEdit,
    QVBoxLayout, QWidget,
)

def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


def field(placeholder: str, name: str, secret: bool = False) -> QLineEdit:
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    widget.setObjectName(name)
    widget.setClearButtonEnabled(True)
    if secret:
        widget.setEchoMode(QLineEdit.EchoMode.Password)
    return widget


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("section")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 13, 16, 15)
    layout.setSpacing(10)
    layout.addWidget(label(title, "sectionTitle"))
    return frame, layout


def form() -> QFormLayout:
    layout = QFormLayout()
    layout.setSpacing(9)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return layout


def _paint_arrow_control(
    widget: QWidget,
    event: QPaintEvent,
    native_paint: Callable[[QPaintEvent], None],
    *,
    spin: bool,
) -> None:
    """Paint the native control and add a visible chevron on top."""
    native_paint(event)
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#36577f" if widget.isEnabled() else "#9aaabd")
    painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    x = widget.width() - (12 if spin else 14)
    middle = widget.height() / 2
    if spin:
        painter.drawPolyline((QPointF(x - 4, middle / 2 + 2), QPointF(x, middle / 2 - 2), QPointF(x + 4, middle / 2 + 2)))
        painter.drawPolyline((QPointF(x - 4, middle + middle / 2 - 2), QPointF(x, middle + middle / 2 + 2), QPointF(x + 4, middle + middle / 2 - 2)))
    else:
        painter.drawPolyline((QPointF(x - 4, middle - 2), QPointF(x, middle + 2), QPointF(x + 4, middle - 2)))


class ReadableSpinBox(QSpinBox):
    """Spin box with visible chevrons independent of the native Qt style."""

    def paintEvent(self, event) -> None:
        _paint_arrow_control(self, event, super().paintEvent, spin=True)


class ReadableTimeEdit(QTimeEdit):
    """Time editor with visible chevrons independent of the native Qt style."""

    def paintEvent(self, event) -> None:
        _paint_arrow_control(self, event, super().paintEvent, spin=True)


class ReadableComboBox(QComboBox):
    """Combo box with a visible down chevron independent of the native Qt style."""

    def paintEvent(self, event) -> None:
        _paint_arrow_control(self, event, super().paintEvent, spin=False)


class ClickableCursorFilter(QObject):
    """Show a hand only while an interactive control is enabled."""

    @staticmethod
    def update_cursor(widget: QWidget) -> None:
        shape = Qt.CursorShape.PointingHandCursor if widget.isEnabled() else Qt.CursorShape.ArrowCursor
        widget.setCursor(shape)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.EnabledChange and isinstance(watched, QWidget):
            self.update_cursor(watched)
        return False
