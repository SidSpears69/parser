"""Offline checks for product-list parsing and bounded JSON transport."""

import io
import unittest
import http.client as http_client
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from web_parser import product_links


FIELDS = product_links.ProductLinkFields(
    items_key="products", product_id_key="id", ozon_key="ozon_url",
    wildberries_key="wb_url", yandex_market_key="market_url",
)


def product(product_id: int | str = 42):
    return {
        "id": product_id,
        "ozon_url": "https://www.ozon.ru/search/?text=rope",
        "wb_url": "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
        "market_url": "https://market.yandex.ru/search?text=rope",
    }


class ProductLinksTests(unittest.TestCase):
    def test_parses_id_and_three_urls_in_feed_order(self):
        records = [product(42), product(" 43 ")]
        result = product_links.parse_product_links({"products": records}, FIELDS)
        self.assertEqual([item.product_id for item in result], ["42", "43"])
        self.assertEqual(result[0].ozon_url, records[0]["ozon_url"])
        self.assertEqual(result[0].wildberries_url, records[0]["wb_url"])
        self.assertEqual(result[0].yandex_market_url, records[0]["market_url"])

    def test_rejects_missing_url_and_duplicate_id(self):
        missing = product()
        del missing["wb_url"]
        with self.assertRaisesRegex(product_links.ProductLinksError, "Wildberries"):
            product_links.parse_product_links({"products": [missing]}, FIELDS)
        with self.assertRaisesRegex(product_links.ProductLinksError, "повторяющийся ID"):
            product_links.parse_product_links({"products": [product(), product("42")]}, FIELDS)

    def test_rejects_bad_shape_or_non_http_url(self):
        with self.assertRaisesRegex(product_links.ProductLinksError, "массив"):
            product_links.parse_product_links({"products": {}}, FIELDS)
        bad = product()
        bad["ozon_url"] = "javascript:alert(1)"
        with self.assertRaisesRegex(product_links.ProductLinksError, "HTTP"):
            product_links.parse_product_links({"products": [bad]}, FIELDS)

    def test_downloads_utf8_json_with_size_limit(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'\xef\xbb\xbf{"products": []}')
        with patch.object(product_links.urlrequest, "build_opener", return_value=opener):
            self.assertEqual(product_links.download_json("https://site.example/feed.json", {}), {"products": []})
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)

        opener.open.return_value = io.BytesIO(b"12345")
        with patch.object(product_links.urlrequest, "build_opener", return_value=opener), \
                patch.object(product_links, "_MAX_RESPONSE_BYTES", 4):
            with self.assertRaisesRegex(product_links.ProductLinksError, "64 МБ"):
                product_links.download_json("https://site.example/feed.json", {})

    def test_rejects_cross_origin_redirect_without_forwarding_token(self):
        handler = product_links._SameOriginRedirect()
        request = Request("https://site.example/feed.json", headers={"Authorization": "Bearer secret"})
        with self.assertRaisesRegex(product_links.ProductLinksError, "другой сервер"):
            handler.redirect_request(request, None, 302, "Found", http_client.HTTPMessage(), "https://other.example/feed.json")

    def test_http_error_message_does_not_expose_url_or_token(self):
        opener = Mock()
        response_error = HTTPError(
            "https://site.example/feed.json?token=secret", 401, "Denied", http_client.HTTPMessage(), None,
        )
        opener.open.side_effect = response_error
        try:
            with patch.object(product_links.urlrequest, "build_opener", return_value=opener):
                with self.assertRaises(product_links.ProductLinksError) as caught:
                    product_links.download_json("https://site.example/feed.json?token=secret", {})
        finally:
            response_error.close()
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
