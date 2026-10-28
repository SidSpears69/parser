"""Shared table layout and row rendering for marketplace search results."""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidgetItem

from web_parser.ozon import OzonProduct
from web_parser.ui.links import LinksPreviewTable
from web_parser.wildberries import WildberriesProduct


_VISIBLE_ROW_LIMIT = 120


class MarketplaceResultsTable(LinksPreviewTable):
    """Compact numeric columns and a hoverable primary photo link."""

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

    def append_products(
        self, product_id: str, products: Sequence[OzonProduct | WildberriesProduct],
    ) -> None:
        """Append parsed products with shared formatting and photo links."""
        for product in products:
            row = self.rowCount()
            if row >= _VISIBLE_ROW_LIMIT:
                break
            self.insertRow(row)
            values = (
                product_id, product.name, f"{product.price:.2f}",
                f"{product.discounted_price:.2f}",
                str(product.rating) if product.rating is not None else "—",
                str(product.reviews), "Открыть фото",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1 and isinstance(product, WildberriesProduct):
                    item.setToolTip(f"Артикул WB: {product.wb_id}")
                if column == 6:
                    item.setForeground(self.LINK_COLOR)
                    item.setData(Qt.ItemDataRole.UserRole, product.image_url)
                    item.setToolTip(product.image_url)
                self.setItem(row, column, item)
