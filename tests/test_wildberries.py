"""Offline checks for the JSON returned by the Wildberries storefront."""

from decimal import Decimal
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from web_parser.wildberries import (
    WildberriesClient, WildberriesError, parse_search_json, search_api_url,
)


ROUTES = {
    "origin": {"mediabasket_route_map": [{"method": "range", "hosts": [
        {"vol_range_from": 5190, "vol_range_to": 5501, "host": "basket-28.wbbasket.ru"},
    ]}]},
}


def product(index: int) -> dict:
    return {
        "id": 519585970 + index,
        "name": f"Шнур {index}",
        "pics": 2,
        "picsUpdate": 2,
        "reviewRating": 4.9,
        "feedbacks": 26,
        "sizes": [{"price": {"basic": 322900, "product": 93000}}],
    }


class WildberriesParserTests(unittest.TestCase):
    def test_search_url_preserves_query_and_region(self):
        url = search_api_url(
            "https://www.wildberries.ru/catalog/0/search.aspx?search=%D0%A8%D0%BD%D1%83%D1%80+Classic&dest=-1257786"
        )
        self.assertIn("/__internal/u-search/exactmatch/ru/common/v18/search", url)
        self.assertEqual(parse_qs(urlsplit(url).query)["query"], ["Шнур Classic"])
        self.assertEqual(parse_qs(urlsplit(url).query)["dest"], ["-1257786"])
        with self.assertRaises(WildberriesError):
            search_api_url("https://example.com/catalog/0/search.aspx?search=rope")
        with self.assertRaises(WildberriesError):
            search_api_url("https://www.wildberries.ru/catalog/0/search.aspx")

    def test_first_six_current_products_and_primary_images(self):
        results = parse_search_json({"products": [product(i) for i in range(8)]}, ROUTES)
        self.assertEqual(len(results), 6)
        first = results[0]
        self.assertEqual(first.wb_id, 519585970)
        self.assertEqual(first.price, Decimal("3229"))
        self.assertEqual(first.discounted_price, Decimal("930"))
        self.assertEqual(first.name, "Шнур 0")
        self.assertEqual(first.rating, Decimal("4.9"))
        self.assertEqual(first.reviews, 26)
        self.assertEqual(
            first.image_url,
            "https://basket-28.wbbasket.ru/vol5195/part519585/519585970/images/big/1.webp?picsUpdate=2",
        )

    def test_legacy_prices_nested_data_and_no_results(self):
        item = product(0)
        del item["sizes"]
        item.update(priceU=12500, salePriceU=9900, rating=4.5)
        del item["reviewRating"]
        result = parse_search_json({"data": {"products": [item]}}, ROUTES)[0]
        self.assertEqual(result.price, Decimal("125"))
        self.assertEqual(result.discounted_price, Decimal("99"))
        self.assertEqual(result.rating, Decimal("4.5"))
        self.assertEqual(parse_search_json({"products": []}, ROUTES), ())

    def test_malformed_json_is_not_mistaken_for_empty_search(self):
        with self.assertRaisesRegex(WildberriesError, "отсутствует список"):
            parse_search_json({"metadata": {}}, ROUTES)
        with self.assertRaisesRegex(WildberriesError, "маршрут"):
            parse_search_json({"products": [product(0)]}, {})
        item = product(0)
        item["sizes"][0]["price"]["basic"] = None
        with self.assertRaisesRegex(WildberriesError, "цены"):
            parse_search_json({"products": [item]}, ROUTES)

    def test_client_reads_text_plain_json_and_reuses_cdn_routes(self):
        search_response = Mock(
            ok=True, body=Mock(return_value=json.dumps({"products": [product(0)]}).encode()),
        )
        routes_response = Mock(ok=True, body=Mock(return_value=json.dumps(ROUTES).encode()))
        request = Mock()
        request.get.side_effect = [search_response, routes_response, search_response]
        playwright = Mock()
        playwright.request.new_context.return_value = request
        starter = Mock()
        starter.start.return_value = playwright
        with patch("web_parser.wildberries.sync_playwright", return_value=starter):
            with WildberriesClient() as client:
                self.assertEqual(
                    client.fetch("https://www.wildberries.ru/catalog/0/search.aspx?search=rope")[0].wb_id,
                    519585970,
                )
                self.assertEqual(
                    client.fetch("https://www.wildberries.ru/catalog/0/search.aspx?search=cord")[0].wb_id,
                    519585970,
                )
        self.assertEqual(request.get.call_count, 3)
        request.dispose.assert_called_once()
        playwright.stop.assert_called_once()

    def test_http_denial_is_reported(self):
        response = Mock(ok=False, status=403)
        request = Mock()
        request.get.return_value = response
        playwright = Mock()
        playwright.request.new_context.return_value = request
        starter = Mock()
        starter.start.return_value = playwright
        with patch("web_parser.wildberries.sync_playwright", return_value=starter):
            with WildberriesClient() as client:
                with self.assertRaisesRegex(WildberriesError, "HTTP 403"):
                    client.fetch("https://www.wildberries.ru/catalog/0/search.aspx?search=rope")

    def test_existing_playwright_session_is_reused_without_stopping_it(self):
        playwright = Mock()
        response = Mock(ok=True, body=Mock(return_value=b'{"products":[]}'))
        playwright.request.new_context.return_value.get.return_value = response
        with patch("web_parser.wildberries.sync_playwright") as start_playwright:
            with WildberriesClient(playwright=playwright) as client:
                self.assertEqual(
                    client.fetch("https://www.wildberries.ru/catalog/0/search.aspx?search=rope"),
                    (),
                )
        start_playwright.assert_not_called()
        playwright.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
