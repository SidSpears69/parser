"""Wildberries HTML, search JSON, and brand filter tests."""

from unittest.mock import Mock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import BrowserAccessDenied, BrowserAccessError, BrowserCaptchaDenied

from .base import WILDBERRIES_BRAND_URL, WILDBERRIES_URL, BrowserAccessTestCase


class WildberriesBrowserTests(BrowserAccessTestCase):
    def test_strict_access_reports_captcha_on_wildberries_json_page(self):
        self.page.evaluate.return_value = True
        with self.access(strict_access=True) as access:
            with self.assertRaises(BrowserCaptchaDenied):
                access.fetch_wildberries_json(WILDBERRIES_URL)
        self.page.goto.assert_called_once()

    def test_wildberries_498_can_resolve_to_a_usable_page(self):
        self.page.goto.return_value = Mock(ok=False, status=498)
        self.page.url = WILDBERRIES_URL
        self.page.content.return_value = "<html><body>Результаты поиска</body></html>"
        with self.access() as access:
            result = access.fetch(WILDBERRIES_URL)
        self.assertEqual(result.status, 498)
        self.assertEqual(result.attempts, 1)
        self.assertIn("Wildberries", self.page.wait_for_function.call_args.args[0])
        self.assertTrue(self.page.wait_for_function.call_args.kwargs["arg"])
        self.assertEqual(self.page.wait_for_function.call_args.kwargs["timeout"], 20_000)
        self.page.goto.assert_called_once()

    def test_wildberries_498_without_usable_page_is_denied(self):
        self.page.goto.return_value = Mock(ok=False, status=498)
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("challenge persists")
        with self.access() as access:
            with self.assertRaises(BrowserAccessDenied) as caught:
                access.fetch(WILDBERRIES_URL)
        self.assertEqual(caught.exception.status, 498)
        self.page.goto.assert_called_once()

    def test_wildberries_browser_fallback_captures_json_response(self):
        api_response = Mock(
            status=200,
            url="https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search?query=rope",
        )
        api_response.body.return_value = b'{"products":[{"id":42}]}'
        listener = []
        self.page.on.side_effect = lambda _event, callback: listener.append(callback)

        def navigate(*_args, **_kwargs):
            listener[0](api_response)
            api_response.body.assert_not_called()
            return self.response

        self.page.goto.side_effect = navigate
        with self.access() as access:
            document = access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(document, {"products": [{"id": 42}]})
        self.page.remove_listener.assert_called_once_with("response", listener[0])

    def test_wildberries_brand_capture_matches_brand_and_filters(self):
        endpoint = "https://www.wildberries.ru/__internal/u-catalog/brands/v4/catalog?"
        wrong_brand = Mock(
            status=200,
            url=endpoint + "brand=123&sort=popular&f14177449=11807341",
        )
        wrong_filter = Mock(
            status=200,
            url=endpoint + "brand=312181720&sort=popular&f14177449=999",
        )
        wrong_page = Mock(
            status=200,
            url=endpoint + "brand=312181720&page=2&sort=popular&f14177449=11807341",
        )
        catalog = Mock(
            status=200,
            url=endpoint + "brand=312181720&sort=popular&f14177449=11807341",
        )
        catalog.body.return_value = b'{"products":[{"id":42}]}'
        listeners = []
        self.page.on.side_effect = lambda _event, callback: listeners.append(callback)

        def navigate(*_args, **_kwargs):
            for response in (wrong_brand, wrong_filter, wrong_page, catalog):
                listeners[0](response)
            return self.response

        self.page.goto.side_effect = navigate
        with self.access() as access:
            document = access.fetch_wildberries_json(WILDBERRIES_BRAND_URL)
        self.assertEqual(document, {"products": [{"id": 42}]})
        wrong_brand.body.assert_not_called()
        wrong_filter.body.assert_not_called()
        wrong_page.body.assert_not_called()
        self.page.goto.assert_called_once_with(
            WILDBERRIES_BRAND_URL, wait_until="domcontentloaded", timeout=30_000,
        )

    def test_wildberries_browser_fallback_uses_json_despite_498_html_check(self):
        api_response = Mock(
            status=200,
            url="https://search.wb.ru/exactmatch/ru/common/v18/search?query=rope",
        )
        api_response.body.return_value = b'{"products":[{"id":42}]}'
        listener = []
        self.page.on.side_effect = lambda _event, callback: listener.append(callback)

        def navigate(*_args, **_kwargs):
            listener[0](api_response)
            return Mock(ok=False, status=498)

        self.page.goto.side_effect = navigate
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("HTML check missed results")
        with self.access() as access:
            document = access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(document, {"products": [{"id": 42}]})
        self.page.wait_for_function.assert_not_called()
        self.page.remove_listener.assert_called_once_with("response", listener[0])

    def test_wildberries_json_arriving_after_498_is_accepted_without_html_cards(self):
        api_response = Mock(
            status=200,
            url="https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search?query=rope",
        )
        api_response.body.return_value = b'{"products":[{"id":42}]}'
        listeners = []
        self.page.on.side_effect = lambda _event, callback: listeners.append(callback)
        self.page.goto.return_value = Mock(ok=False, status=498)

        def receive_json(*_args, **_kwargs):
            listeners[0](api_response)

        self.page.wait_for_timeout.side_effect = receive_json
        with patch("web_parser.browser_access.wildberries.time.monotonic", side_effect=[0, 1]):
            with self.access(attempts=1) as access:
                document = access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(document, {"products": [{"id": 42}]})
        self.page.goto.assert_called_once_with(
            WILDBERRIES_URL, wait_until="domcontentloaded", timeout=30_000,
        )
        self.page.wait_for_function.assert_not_called()
        self.page.wait_for_event.assert_not_called()

    def test_wildberries_browser_fallback_ignores_later_filters_json(self):
        api_url = "https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search?query=rope"
        products_response = Mock(status=200, url=api_url)
        products_response.body.return_value = b'{"products":[{"id":42}]}'
        filters_response = Mock(status=200, url=api_url + "&autoselectFilters=false")
        filters_response.body.return_value = b'{"data":{"filters":[],"total":1}}'
        listener = []
        self.page.on.side_effect = lambda _event, callback: listener.append(callback)

        def navigate(*_args, **_kwargs):
            listener[0](products_response)
            listener[0](filters_response)
            return Mock(ok=False, status=498)

        self.page.goto.side_effect = navigate
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("HTML check missed results")
        with self.access() as access:
            document = access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(document, {"products": [{"id": 42}]})

    def test_wildberries_browser_fallback_ignores_other_search_query(self):
        api_response = Mock(
            status=200,
            url="https://search.wb.ru/exactmatch/ru/common/v18/search?query=another",
        )
        api_response.body.return_value = b'{"products":[{"id":42}]}'
        listener = []
        self.page.on.side_effect = lambda _event, callback: listener.append(callback)

        def navigate(*_args, **_kwargs):
            listener[0](api_response)
            return self.response

        self.page.goto.side_effect = navigate
        with patch("web_parser.browser_access.wildberries.time.monotonic", side_effect=[0, 46]):
            with self.access(attempts=1) as access:
                with self.assertRaisesRegex(BrowserAccessError, "не появился JSON"):
                    access.fetch_wildberries_json(WILDBERRIES_URL)

    def test_wildberries_browser_fallback_keeps_498_without_json(self):
        self.page.goto.return_value = Mock(ok=False, status=498)
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("challenge persists")
        with patch("web_parser.browser_access.wildberries.time.monotonic", side_effect=[0, 46]):
            with self.access(attempts=1) as access:
                with self.assertRaises(BrowserAccessDenied) as caught:
                    access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(caught.exception.status, 498)
        self.page.remove_listener.assert_called_once()

    def test_strict_access_stops_after_unresolved_wildberries_challenge(self):
        self.page.goto.return_value = Mock(ok=False, status=498)
        with patch("web_parser.browser_access.wildberries.time.monotonic", side_effect=[0, 46]):
            with self.access(strict_access=True) as access:
                with self.assertRaises(BrowserAccessDenied) as caught:
                    access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(caught.exception.status, 498)
        self.page.goto.assert_called_once()

    def test_strict_access_keeps_rate_limit_from_wildberries_search_json(self):
        self.page.on.side_effect = lambda _event, callback: callback(Mock(
            status=429, url="https://search.wb.ru/exactmatch/ru/common/v18/search?query=rope",
        ))
        with self.access(strict_access=True) as access:
            with self.assertRaises(BrowserAccessDenied) as caught:
                access.fetch_wildberries_json(WILDBERRIES_URL)
        self.assertEqual(caught.exception.status, 429)
        self.page.goto.assert_called_once()

    def test_wildberries_browser_fallback_rejects_missing_json(self):
        with patch("web_parser.browser_access.wildberries.time.monotonic", side_effect=[0, 46]):
            with self.access(attempts=1) as access:
                with self.assertRaisesRegex(BrowserAccessError, "не появился JSON"):
                    access.fetch_wildberries_json(WILDBERRIES_URL)
        self.page.remove_listener.assert_called_once()

    def test_wildberries_json_permanent_http_error_does_not_wait(self):
        self.page.goto.return_value = Mock(ok=False, status=404)
        with self.access(attempts=1) as access:
            with self.assertRaisesRegex(BrowserAccessError, "HTTP 404"):
                access.fetch_wildberries_json(WILDBERRIES_URL)
        self.page.wait_for_timeout.assert_not_called()
