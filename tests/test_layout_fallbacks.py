"""Rendered fallback card extraction and layout change notices."""

from decimal import Decimal
import json
import unittest
from unittest.mock import Mock

from playwright.sync_api import sync_playwright

from web_parser.browser_access import BrowserPageAccess
from web_parser.ozon import parse_ozon_search
from web_parser.yandex_market import parse_yandex_search


class LayoutFallbackTests(unittest.TestCase):
    def test_yandex_optional_discount_does_not_trigger_layout_notice(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(headless=True)
            try:
                page = browser.new_page()
                access = Mock(spec=BrowserPageAccess, page=page)
                markup = """
                    <article data-zone-name="productSnippet" data-zone-data='CARD_DATA'>
                        <a href="/product--shnur/123">
                            <h3 data-auto="snippet-title">Шнур яхтенный</h3>
                            <div data-auto="galleryLink">
                                <img src="https://avatars.mds.yandex.net/get-mpic/123/1/orig">
                            </div>
                        </a>
                        <span data-auto="snippet-price-current">CURRENT_PRICE ₽</span>
                    </article>
                """
                for details, visible, discounted, expected_warning in (
                    ({"price": 1200}, "1 200", Decimal("1200"), False),
                    ({"price": 1200}, "900", Decimal("900"), False),
                    ({"price": 1200, "additionalPrices": [
                        {"priceType": "withDiscount", "priceValue": None},
                    ]}, "900", Decimal("900"), True),
                    ({"price": 1200, "additionalPrices": [
                        {"priceType": "withDiscount", "priceValue": 900},
                    ]}, "900", Decimal("900"), False),
                ):
                    with self.subTest(details=details, visible=visible):
                        html = markup.replace("CARD_DATA", json.dumps(details))
                        page.set_content(html.replace("CURRENT_PRICE", visible), wait_until="domcontentloaded")
                        notices: list[str] = []
                        products = parse_yandex_search(access, on_layout_change=notices.append)
                        self.assertEqual(products[0].price, Decimal("1200"))
                        self.assertEqual(products[0].discounted_price, discounted)
                        self.assertEqual(bool(notices), expected_warning)
                        if expected_warning:
                            self.assertIn("цена со скидкой из DOM", notices[0])
            finally:
                browser.close()

    def test_alternate_ozon_and_yandex_markup_is_reported(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(headless=True)
            try:
                page = browser.new_page()
                alternate_card = """
                    <section class="tile-root" data-index="0">
                        <a href="/product/test-1">
                            <img src="https://ir.ozone.ru/s3/photo.jpg" alt="Парусный шнур">
                        </a>
                        <span data-auto="price-value">1 234 ₽</span>
                    </section>
                """
                page.set_content(alternate_card * 6)
                notices: list[str] = []
                access = Mock(spec=BrowserPageAccess, page=page)
                ozon = parse_ozon_search(access, on_layout_change=notices.append)
                self.assertEqual(ozon[0].name, "Парусный шнур")
                self.assertEqual(ozon[0].price, Decimal("1234"))
                self.assertEqual(len(notices), 1)
                self.assertIn("карточки", notices[0])
                self.assertIn("название", notices[0])

                primary_card = """
                    <div class="tile-root" data-index="0">
                        <a href="/product/test-2">
                            <img src="https://ir.ozone.ru/s3/photo.jpg">
                            <span class="tsBody500Medium">Швартовый шнур</span>
                        </a>
                        <span class="tsHeadline500Medium">750 ₽</span>
                    </div>
                """
                page.set_content('<div data-widget="tileGridDesktop">' + primary_card * 6 + '</div>')
                notices.clear()
                primary = parse_ozon_search(access, on_layout_change=notices.append)
                self.assertEqual(primary[0].name, "Швартовый шнур")
                self.assertEqual(notices, [])

                page.set_content("""
                    <article data-auto="snippet">
                        <a href="/product--shnur/123">
                            <h3>Шнур яхтенный</h3>
                            <img src="https://avatars.mds.yandex.net/get-mpic/123/1/orig">
                        </a>
                        <span data-auto="snippet-price-current">900 ₽</span>
                    </article>
                """)
                notices.clear()
                yandex = parse_yandex_search(access, on_layout_change=notices.append)
                self.assertEqual(yandex[0].name, "Шнур яхтенный")
                self.assertEqual(yandex[0].price, Decimal("900"))
                self.assertEqual(yandex[0].discounted_price, Decimal("900"))
                self.assertEqual(len(notices), 1)
                self.assertIn("карточки", notices[0])
                self.assertIn("цена из DOM", notices[0])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
