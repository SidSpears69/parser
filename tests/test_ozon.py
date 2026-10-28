"""Ozon card fields from a rendered search page."""

from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import PropertyMock, patch

from web_parser.browser_access import BrowserPageAccess
from web_parser.browser_discovery import BrowserInstallation
from web_parser.ozon import OzonError, parse_ozon_cards, parse_ozon_search


def card(**changes: object) -> dict[str, object]:
    item: dict[str, object] = {
        "image_url": "https://ir.ozone.ru/s3/multimedia/photo.jpg",
        "current_price": "11\u2009747\u2009₽",
        "original_price": "28\u2009650\u2009₽28\u2009650\u2009₽",
        "name": "Шнур яхтенный Classic",
        "rating": "5.0",
        "reviews": "23\u00a0отзыва",
    }
    item.update(changes)
    return item


class OzonTests(unittest.TestCase):
    def test_first_six_cards_and_all_six_fields(self):
        products = parse_ozon_cards([card(name=f"Шнур {index}") for index in range(8)])
        self.assertEqual(len(products), 6)
        first = products[0]
        self.assertEqual(first.image_url, "https://ir.ozone.ru/s3/multimedia/photo.jpg")
        self.assertEqual(first.price, Decimal("28650.00"))
        self.assertEqual(first.discounted_price, Decimal("11747.00"))
        self.assertEqual(first.name, "Шнур 0")
        self.assertEqual(first.rating, Decimal("5.0"))
        self.assertEqual(first.reviews, 23)
        self.assertEqual(products[-1].name, "Шнур 5")

    def test_card_without_discount_or_reviews(self):
        product, = parse_ozon_cards([card(
            original_price=None, rating=None, reviews=None,
            current_price="16\u2009991\u2009₽",
        )])
        self.assertEqual(product.price, Decimal("16991.00"))
        self.assertEqual(product.discounted_price, product.price)
        self.assertIsNone(product.rating)
        self.assertEqual(product.reviews, 0)

    def test_compact_review_count(self):
        product, = parse_ozon_cards([card(reviews="1,2 тыс. отзывов", rating="4,7")])
        self.assertEqual(product.reviews, 1200)
        self.assertEqual(product.rating, Decimal("4.7"))

    def test_missing_required_field_is_reported(self):
        with self.assertRaisesRegex(OzonError, "№1: нет основного фото"):
            parse_ozon_cards([card(image_url=None)])
        with self.assertRaisesRegex(OzonError, "№1: нет названия"):
            parse_ozon_cards([card(name="")])

    def test_no_cards_is_distinct_from_empty_search(self):
        class FakePage:
            @staticmethod
            def wait_for_function(*_args, **_kwargs):
                return None

            @staticmethod
            def evaluate(expression, *_args):
                return "ничего не найдено" in expression if "ничего не найдено" in expression else []

        access = BrowserPageAccess(
            BrowserInstallation("Google Chrome", Path(__file__)), 0,
        )
        with patch.object(BrowserPageAccess, "page", new_callable=PropertyMock) as page:
            page.return_value = FakePage()
            self.assertEqual(parse_ozon_search(access), ())


if __name__ == "__main__":
    unittest.main()
