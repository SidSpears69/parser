"""Preview of the first six Wildberries search results per source product."""

from PySide6.QtWidgets import QHeaderView

from web_parser.ui.links import LinksPreviewTable


class WildberriesResultsTable(LinksPreviewTable):
    """Compact numeric columns and a hoverable link to each primary photo."""

    def __init__(self):
        super().__init__()
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels((
            "ID", "Название", "Цена", "Со скидкой", "Рейтинг", "Отзывы", "Фото",
        ))
        header = self.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((2, 95), (3, 105), (4, 80), (5, 80), (6, 125)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
