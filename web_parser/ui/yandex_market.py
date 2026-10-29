"""Yandex Market result preview table."""

from web_parser.ui.results import MarketplaceResultsTable


class YandexMarketResultsTable(MarketplaceResultsTable):
    """Show the first six search products and their primary photos."""

    def __init__(self):
        super().__init__()
        # The search card reports the number of ratings, not written reviews.
        self.setHorizontalHeaderLabels((
            "ID", "Название", "Цена", "Со скидкой", "Рейтинг", "Оценки", "Фото",
        ))
