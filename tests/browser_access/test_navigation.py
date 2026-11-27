"""Navigation retries, access errors, and request cancellation tests."""

import socket
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError, BrowserFetchFailed,
    BrowserLoginRequired, BrowserPageAccess, BrowserProxyAuthenticationRequired,
    BrowserProxyConnectionFailed, BrowserSessionClosed,
)

from .base import CHROME, OZON_URL, URL, WILDBERRIES_URL, YANDEX_URL, BrowserAccessTestCase


class BrowserNavigationTests(BrowserAccessTestCase):
    def test_empty_page_is_retried_after_pause(self):
        self.page.wait_for_function.side_effect = [PlaywrightTimeoutError("empty"), None]
        with self.access() as access:
            result = access.fetch(URL)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(self.page.goto.call_count, 2)
        self.sleep.assert_called_once_with(2)

    def test_delay_between_urls_reuses_same_page(self):
        with self.access() as access:
            access.fetch(URL)
            access.fetch("https://other.example/item")
        self.context.new_page.assert_called_once_with()
        self.assertEqual(self.page.goto.call_count, 2)
        self.sleep.assert_called_once_with(2)

    def test_stop_during_pause_prevents_next_navigation(self):
        stopped = False

        def stop_during_pause(_seconds):
            nonlocal stopped
            stopped = True

        access = BrowserPageAccess(
            CHROME, 2, sleep=stop_during_pause, cancel_requested=lambda: stopped,
        )
        with access:
            access.fetch(URL)
            with self.assertRaises(BrowserAccessCancelled):
                access.fetch("https://other.example/item")
        self.page.goto.assert_called_once()

    def test_transient_status_and_browser_error_are_retried(self):
        self.page.goto.side_effect = [
            Mock(ok=False, status=429), PlaywrightError("temporary network error"), self.response,
        ]
        with self.access() as access:
            result = access.fetch(URL)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_navigation_timeout_after_all_attempts_is_recoverable(self):
        self.page.goto.side_effect = PlaywrightTimeoutError("Timeout 30000ms exceeded")
        with self.access() as access:
            with self.assertRaisesRegex(BrowserFetchFailed, "Попыток: 3"):
                access.fetch(YANDEX_URL)
        self.assertEqual(self.page.goto.call_count, 3)

    def test_proxy_connection_failure_is_distinct_in_html_and_json_navigation(self):
        proxy = {"server": "http://proxy.example:8080"}
        for code, method, url in (
            ("Page.goto: net::ERR_PROXY_CONNECTION_FAILED", "fetch", OZON_URL),
            (": net::ERR_PROXY_CONNECTION_FAILED", "fetch", OZON_URL),
            ("NS_ERROR_PROXY_CONNECTION_REFUSED", "fetch_wildberries_json", WILDBERRIES_URL),
        ):
            with self.subTest(code=code, method=method):
                self.page.goto.reset_mock(side_effect=True)
                self.page.goto.side_effect = PlaywrightError(f"{code} at {url}")
                with self.access(proxy=proxy, strict_access=True) as access:
                    with self.assertRaises(BrowserProxyConnectionFailed):
                        getattr(access, method)(url)
                self.page.goto.assert_called_once()
        self.assertEqual(self.page.remove_listener.call_count, 3)

    def test_ozon_navigation_failure_reports_document_status(self):
        def navigate(*_args, **_kwargs):
            listener = self.page.on.call_args.args[1]
            request = Mock(frame=self.page.main_frame)
            request.is_navigation_request.return_value = True
            listener(Mock(url=OZON_URL, status=307, request=request))
            listener(Mock(url=OZON_URL, status=403, request=request))
            image_request = Mock(frame=self.page.main_frame)
            image_request.is_navigation_request.return_value = False
            listener(Mock(url=OZON_URL, status=404, request=image_request))
            raise PlaywrightError(f"Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at {OZON_URL}")

        self.page.goto.side_effect = navigate
        with self.access(strict_access=True) as access:
            with self.assertRaisesRegex(BrowserFetchFailed, "HTTP 403"):
                access.fetch(OZON_URL)
        self.page.goto.assert_called_once()
        self.page.remove_listener.assert_called_once_with("response", self.page.on.call_args.args[1])

    def test_ozon_navigation_failure_with_http_503_can_retry(self):
        def navigate(*_args, **_kwargs):
            if self.page.goto.call_count > 1:
                return self.response
            listener = self.page.on.call_args.args[1]
            request = Mock(frame=self.page.main_frame)
            request.is_navigation_request.return_value = True
            listener(Mock(url=OZON_URL, status=503, request=request))
            raise PlaywrightError(f"Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at {OZON_URL}")

        self.page.goto.side_effect = navigate
        with self.access(strict_access=True) as access:
            result = access.fetch(OZON_URL)
        self.assertEqual((result.status, result.attempts), (200, 2))
        self.sleep.assert_called_once_with(2)

    def test_ozon_navigation_reports_407_as_proxy_authentication_failure(self):
        proxy = {"server": "http://proxy.example:8080"}

        def navigate(*_args, **_kwargs):
            listener = self.page.on.call_args.args[1]
            request = Mock(frame=self.page.main_frame)
            request.is_navigation_request.return_value = True
            listener(Mock(url=OZON_URL, status=407, request=request))
            raise PlaywrightError(f"Page.goto: net::{code} at {OZON_URL}")

        self.page.goto.side_effect = navigate
        for code in ("ERR_HTTP_RESPONSE_CODE_FAILURE", "ERR_INVALID_AUTH_CREDENTIALS"):
            with self.subTest(code=code), self.access(proxy=proxy, strict_access=True) as access:
                self.page.goto.reset_mock()
                with self.assertRaisesRegex(BrowserProxyAuthenticationRequired, "HTTP 407"):
                    access.fetch(OZON_URL)
                self.page.goto.assert_called_once()

    def test_closed_browser_session_is_not_retried(self):
        self.page.goto.side_effect = PlaywrightError("Page.goto: Target page, context or browser has been closed.")
        with self.access() as access:
            with self.assertRaises(BrowserSessionClosed):
                access.fetch(OZON_URL)
        self.page.goto.assert_called_once()
        self.sleep.assert_not_called()

    def test_ambiguous_tunnel_error_does_not_flag_proxy_connection_failure(self):
        self.page.goto.side_effect = PlaywrightError(
            f"Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at {OZON_URL}",
        )
        with self.access(proxy={"server": "http://proxy.example:8080"}, strict_access=True) as access:
            with self.assertRaises(BrowserFetchFailed) as caught:
                access.fetch(OZON_URL)
        self.assertIs(type(caught.exception), BrowserFetchFailed)
        self.assertEqual(self.page.goto.call_count, 3)

    def test_socks5_error_without_api_name_checks_upstream_greeting(self):
        self.page.goto.side_effect = PlaywrightError(f": net::ERR_SOCKS_CONNECTION_FAILED at {URL}")
        with (
            patch("web_parser.browser_access.navigation.responds_to_socks5_greeting", return_value=False) as probe,
            self.access(proxy={"server": "socks5://proxy.example:1080"}, strict_access=True) as access,
        ):
            with self.assertRaises(BrowserProxyConnectionFailed):
                access.fetch(URL)
        probe.assert_called_once_with(("proxy.example", 1080), authenticated=False, timeout=2)
        self.page.goto.assert_called_once()

    def test_socks5_connection_error_checks_for_upstream_greeting(self):
        self.page.goto.side_effect = PlaywrightError(
            f"Page.goto: net::ERR_SOCKS_CONNECTION_FAILED at {OZON_URL}",
        )
        for case, authenticated, connection_error, replies, expected in (
            ("TCP refused", True, ConnectionRefusedError(), (), BrowserProxyConnectionFailed),
            ("empty greeting", True, None, (b"",), BrowserProxyConnectionFailed),
            ("greeting timeout", True, None, (TimeoutError(),), BrowserProxyConnectionFailed),
            ("split valid greeting", True, None, (b"\x05", b"\x02"), BrowserFetchFailed),
            ("auth method refused", True, None, (b"\x05\xff",), BrowserFetchFailed),
            ("unauthenticated server offline", False, None, (b"",), BrowserProxyConnectionFailed),
        ):
            with self.subTest(case=case):
                proxy = {"server": "socks5://proxy.example:1080"}
                if authenticated:
                    proxy.update(username="user", password="password")
                stream = Mock(spec=socket.socket)
                stream.recv.side_effect = replies
                with (
                    patch("web_parser.browser_access.session.Socks5AuthRelay") as relay,
                    patch(
                        "web_parser.socks5_relay.socket.create_connection", side_effect=connection_error,
                    ) as connect,
                ):
                    relay.return_value.start.return_value = 12345
                    connect.return_value.__enter__.return_value = stream
                    with self.access(attempts=1, proxy=proxy, strict_access=True) as access:
                        with self.assertRaises(BrowserFetchFailed) as caught:
                            access.fetch(OZON_URL)
                self.assertIs(type(caught.exception), expected)
                connect.assert_called_once_with(("proxy.example", 1080), timeout=2)
                if connection_error is None:
                    stream.sendall.assert_called_once_with(b"\x05\x01\x02" if authenticated else b"\x05\x01\x00")

    def test_strict_access_stops_after_http_auth_or_rate_limit(self):
        for status in (401, 429):
            for method, url in (("fetch", OZON_URL), ("fetch_wildberries_json", WILDBERRIES_URL)):
                with self.subTest(status=status, method=method):
                    self.page.goto.reset_mock()
                    self.page.goto.return_value = Mock(ok=False, status=status)
                    with self.access(strict_access=True) as access:
                        with self.assertRaises(BrowserAccessDenied) as caught:
                            getattr(access, method)(url)
                    self.assertEqual(caught.exception.status, status)
                    self.page.goto.assert_called_once()

    def test_strict_access_reports_redirect_to_login(self):
        self.page.url = "https://www.ozon.ru/login?return=%2Fsearch"
        with self.access(strict_access=True) as access:
            with self.assertRaises(BrowserLoginRequired) as caught:
                access.fetch(OZON_URL)
        self.assertEqual(caught.exception.status, 200)
        self.page.goto.assert_called_once()

    def test_permanent_http_error_stops_without_retry(self):
        self.page.goto.return_value = Mock(ok=False, status=404)
        with self.access() as access:
            with self.assertRaisesRegex(BrowserAccessError, "HTTP 404"):
                access.fetch(URL)
        self.page.goto.assert_called_once()
        self.sleep.assert_not_called()

    def test_access_denial_is_distinct_and_not_retried(self):
        for status in (403, 498):
            with self.subTest(status=status):
                self.page.goto.reset_mock()
                self.page.goto.return_value = Mock(ok=False, status=status)
                with self.access() as access:
                    with self.assertRaises(BrowserAccessDenied) as caught:
                        access.fetch(URL)
                self.assertEqual(caught.exception.status, status)
                self.page.goto.assert_called_once()
        self.sleep.assert_not_called()

    def test_exhausted_empty_responses_raise(self):
        self.page.goto.return_value = None
        with self.access(attempts=2) as access:
            with self.assertRaisesRegex(BrowserAccessError, "пустой ответ"):
                access.fetch(URL)
        self.assertEqual(self.page.goto.call_count, 2)
        self.sleep.assert_called_once_with(2)

    def test_invalid_url_and_unopened_session(self):
        access = self.access()
        with self.assertRaisesRegex(BrowserAccessError, "Сначала откройте"):
            access.fetch(URL)
        with access:
            with self.assertRaisesRegex(BrowserAccessError, "HTTP"):
                access.fetch("file:///etc/passwd")
        self.page.goto.assert_not_called()
