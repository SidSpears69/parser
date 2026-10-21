"""Product-link preview table and hover rendering."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
)

class LinksPreviewDelegate(QStyledItemDelegate):
    """Preserve link hover colors when the row is selected."""

    def __init__(self, table: "LinksPreviewTable"):
        super().__init__(table)
        self.table = table

    def paint(self, painter, option, index) -> None:
        if (self.table.hovered_link == (index.row(), index.column())
                and option.state & QStyle.StateFlag.State_Selected):
            option = QStyleOptionViewItem(option)
            option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#c8dcff"))
            option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1b50b6"))
        super().paint(painter, option, index)


class LinksPreviewTable(QTableWidget):
    """Keep IDs compact and give clickable links a distinct hover state."""

    LINK_COLOR = QColor("#245fd2")
    _LINK_HOVER_COLOR = QColor("#1b50b6")
    _LINK_HOVER_BACKGROUND = QBrush(QColor("#e8f0ff"))

    def __init__(self):
        super().__init__(0, 4)
        self._hovered_link: tuple[int, int] | None = None
        self.setItemDelegate(LinksPreviewDelegate(self))
        self.setMouseTracking(True)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        for column in range(1, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 64)

    @property
    def hovered_link(self) -> tuple[int, int] | None:
        return self._hovered_link

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = max(40, self.viewport().width() // 16)
        if self.columnWidth(0) != width:
            self.setColumnWidth(0, width)

    def mouseMoveEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        item = self.itemFromIndex(index) if index.isValid() else None
        link = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        hovered = (index.row(), index.column()) if isinstance(link, str) else None
        self._set_hovered_link(hovered)
        shape = Qt.CursorShape.PointingHandCursor if hovered is not None else Qt.CursorShape.ArrowCursor
        self.viewport().setCursor(shape)
        super().mouseMoveEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.Leave:
            self.reset_link_hover()
        return super().viewportEvent(event)

    def reset_link_hover(self) -> None:
        self._set_hovered_link(None)
        self.viewport().unsetCursor()

    def _set_hovered_link(self, hovered: tuple[int, int] | None) -> None:
        previous_link = self._hovered_link
        if hovered == previous_link:
            return
        if previous_link is not None:
            item = self.item(previous_link[0], previous_link[1])
            if item is not None:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
                item.setForeground(self.LINK_COLOR)
                font = item.font()
                font.setUnderline(False)
                item.setFont(font)
        self._hovered_link = hovered
        if hovered is not None:
            item = self.item(hovered[0], hovered[1])
            if item is not None:
                item.setBackground(self._LINK_HOVER_BACKGROUND)
                item.setForeground(self._LINK_HOVER_COLOR)
                font = item.font()
                font.setUnderline(True)
                item.setFont(font)
