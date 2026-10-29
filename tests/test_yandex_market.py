"""Offline checks for Yandex Market card extraction and field validation."""

from decimal import Decimal
import unittest
from unittest.mock import Mock

from web_parser.yandex_market import (
    YandexMarketError, parse_yandex_cards, parse_yandex_search,
)


def _card(index: int) -> dict[str, object]:
    return {
        "image_url": f"https://avatars.mds.yandex.net/get-mpic/123/{index}/orig",
        "price": 17500,
        "discounted_price": "3812",
        "name": f"Шнур {index}",
        "rating": "4.9",
        "reviews": "1\u202f234",
    }


class YandexMarketTests(unittest.TestCase):
    def test_first_six_cards_keep_order_and_all_six_fields(self):
        products = parse_yandex_cards([_card(index) for index in range(8)])
        self.assertEqual(len(products), 6)
        self.assertEqual([product.name for product in products], [
            f"Шнур {index}" for index in range(6)
        ])
        self.assertEqual(products[0].image_url, _card(0)["image_url"])
        self.assertEqual(products[0].price, Decimal("17500"))
        self.assertEqual(products[0].discounted_price, Decimal("3812"))
        self.assertEqual(products[0].rating, Decimal("4.9"))
        self.assertEqual(products[0].reviews, 1234)

    def test_missing_rating_and_reviews_are_allowed(self):
        card = _card(0)
        card["rating"] = None
        card["reviews"] = None
        product = parse_yandex_cards([card])[0]
        self.assertIsNone(product.rating)
        self.assertEqual(product.reviews, 0)

    def test_missing_photo_and_price_are_reported(self):
        card = _card(0)
        card["image_url"] = "https://example.org/image.jpg"
        with self.assertRaisesRegex(YandexMarketError, "основного фото"):
            parse_yandex_cards([card])
        card = _card(0)
        card["price"] = None
        with self.assertRaisesRegex(YandexMarketError, "нет поля «цена»"):
            parse_yandex_cards([card])

    def test_empty_search_is_not_an_error(self):
        page = Mock()
        page.evaluate.side_effect = [[], True]
        access = Mock(page=page)
        self.assertEqual(parse_yandex_search(access), ())

    def test_missing_cards_without_empty_message_is_an_error(self):
        page = Mock()
        page.evaluate.side_effect = [[], False]
        access = Mock(page=page)
        with self.assertRaisesRegex(YandexMarketError, "не найдены карточки"):
            parse_yandex_search(access)


if __name__ == "__main__":
    unittest.main()
