"""Rendered page, challenge detection, and search readiness tests."""

from unittest.mock import Mock, call

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import (
    BrowserAccessDenied, BrowserAccessError, BrowserCaptchaDenied, BrowserFetchFailed, YandexCaptchaDenied,
)

from .base import OZON_URL, YANDEX_URL, BrowserAccessTestCase


class BrowserPageTests(BrowserAccessTestCase):
    def test_ozon_403_can_resolve_to_search_results(self):
        self.page.goto.return_value = Mock(ok=False, status=403)
        self.page.url = OZON_URL
        self.page.content.return_value = "<html><body>Товары Ozon</body></html>"
        with self.access() as access:
            result = access.fetch(OZON_URL)
        self.assertEqual(result.status, 403)
        self.assertEqual(result.attempts, 1)
        self.assertIn("/product/", self.page.wait_for_function.call_args.args[0])
        self.assertTrue(self.page.wait_for_function.call_args.kwargs["arg"])
        self.assertEqual(self.page.wait_for_function.call_args.kwargs["timeout"], 20_000)
        self.page.goto.assert_called_once()

    def test_ozon_403_without_results_is_denied(self):
        self.page.goto.return_value = Mock(ok=False, status=403)
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("challenge persists")
        with self.access() as access:
            with self.assertRaises(BrowserAccessDenied) as caught:
                access.fetch(OZON_URL)
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("капча или проверка браузера Ozon", str(caught.exception))
        self.page.goto.assert_called_once()

    def test_strict_access_reports_explicit_ozon_captcha(self):
        self.page.goto.return_value = Mock(ok=False, status=403)
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("challenge persists")
        self.page.evaluate.return_value = True
        with self.access(strict_access=True) as access:
            with self.assertRaises(BrowserCaptchaDenied):
                access.fetch(OZON_URL)
        self.page.goto.assert_called_once()

    def test_strict_access_reports_captcha_on_successful_ozon_page(self):
        self.page.evaluate.return_value = True
        with self.access(strict_access=True) as access:
            with self.assertRaises(BrowserCaptchaDenied):
                access.fetch(OZON_URL)
        self.page.goto.assert_called_once()

    def test_yandex_captcha_with_http_200_retries_up_to_three_attempts(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.return_value = True
        with self.access() as access:
            with self.assertRaisesRegex(YandexCaptchaDenied, "HTTP 200: Обнаружена капча.*Попыток: 3"):
                access.fetch(YANDEX_URL)
        self.assertEqual(
            self.page.goto.call_args_list,
            [call(YANDEX_URL, wait_until="domcontentloaded", timeout=30_000)] * 3,
        )
        self.page.content.assert_not_called()
        self.assertEqual(self.sleep.call_args_list, [call(2), call(2)])

    def test_yandex_captcha_can_clear_before_the_final_attempt(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.side_effect = [True, False, False, "results"]
        with self.access() as access:
            result = access.fetch(YANDEX_URL)
        self.assertEqual((result.status, result.attempts), (200, 2))
        self.assertEqual(self.page.goto.call_count, 2)
        self.context.new_page.assert_called_once()
        self.sleep.assert_called_once_with(2)

    def test_yandex_captcha_has_priority_over_rate_limit_status(self):
        self.page.url = YANDEX_URL
        self.page.goto.return_value = Mock(ok=False, status=429)
        self.page.evaluate.return_value = True
        with self.access(strict_access=True) as access:
            with self.assertRaisesRegex(YandexCaptchaDenied, "HTTP 429: Обнаружена капча"):
                access.fetch(YANDEX_URL)
        self.assertEqual(self.page.goto.call_count, 3)

    def test_yandex_captcha_uses_remaining_navigation_budget(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.return_value = True
        with self.access() as access:
            with self.assertRaises(YandexCaptchaDenied) as caught:
                access.fetch(YANDEX_URL, attempts=2)
        self.assertEqual(caught.exception.attempts, 2)
        self.assertEqual(self.page.goto.call_count, 2)

    def test_yandex_captcha_redirect_is_denied(self):
        self.page.url = "https://yandex.ru/showcaptcha?retpath=market.yandex.ru"
        with self.access() as access:
            with self.assertRaises(YandexCaptchaDenied):
                access.fetch(YANDEX_URL)
        self.page.evaluate.assert_not_called()
        self.assertEqual(self.page.goto.call_count, 3)

    def test_yandex_captcha_redirect_is_detected_after_navigation_timeout(self):
        def navigate(*_args, **_kwargs):
            self.page.url = "https://yandex.ru/showcaptcha?retpath=market.yandex.ru"
            raise PlaywrightTimeoutError("navigation timed out")

        self.page.goto.side_effect = navigate
        with self.access(strict_access=True) as access:
            with self.assertRaises(YandexCaptchaDenied) as caught:
                access.fetch(YANDEX_URL)
        self.assertIsNone(caught.exception.status)
        self.assertEqual(self.page.goto.call_count, 3)
        self.page.evaluate.assert_not_called()

    def test_yandex_captcha_is_detected_in_new_document_without_response(self):
        def navigate(*_args, **_kwargs):
            self.page.url = YANDEX_URL
            return None

        self.page.goto.side_effect = navigate
        self.page.evaluate.return_value = True
        with self.access(strict_access=True) as access:
            with self.assertRaises(YandexCaptchaDenied) as caught:
                access.fetch(YANDEX_URL)
        self.assertIsNone(caught.exception.status)
        self.assertEqual(self.page.goto.call_count, 3)

    def test_yandex_navigation_failure_ignores_old_page_with_captcha(self):
        old_url = "https://yandex.ru/showcaptcha?retpath=market.yandex.ru"
        self.page.url = old_url

        def navigate(*_args, **_kwargs):
            self.page.url = old_url + "#already-open"
            raise PlaywrightTimeoutError("navigation timed out")

        self.page.goto.side_effect = navigate
        with self.access(attempts=1, strict_access=True) as access:
            with self.assertRaisesRegex(BrowserFetchFailed, "navigation timed out"):
                access.fetch(YANDEX_URL)
        self.page.evaluate.assert_not_called()

    def test_yandex_search_requires_results_before_success(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.side_effect = [False, False, "results"]
        with self.access() as access:
            result = access.fetch(YANDEX_URL)
        self.assertEqual(result.status, 200)
        self.page.goto.assert_called_once_with(
            YANDEX_URL, wait_until="domcontentloaded", timeout=30_000,
        )
        self.assertIn("searchOrganic", self.page.wait_for_function.call_args.args[0])
        self.assertEqual(self.page.wait_for_function.call_args.kwargs["timeout"], 10_000)
        self.page.wait_for_timeout.assert_called_once_with(750)
        self.assertEqual(self.page.evaluate.call_count, 3)

    def test_yandex_site_error_retries_with_explicit_reason(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.side_effect = [False, False, "site_error"] * 3
        with self.access() as access:
            with self.assertRaisesRegex(
                BrowserAccessError, "Яндекс Маркет показал страницу «Что-то не сработало»",
            ):
                access.fetch(YANDEX_URL)
        self.assertEqual(self.page.goto.call_count, 3)
        self.assertEqual(self.page.wait_for_function.call_count, 3)
        self.assertIn("Что-то не сработало", self.page.wait_for_function.call_args.args[0])
        self.assertEqual(self.sleep.call_count, 2)
        self.page.content.assert_not_called()

    def test_yandex_site_error_can_clear_on_retry(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.side_effect = [
            False, False, "site_error", False, False, "results",
        ]
        with self.access() as access:
            result = access.fetch(YANDEX_URL)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(self.page.goto.call_count, 2)
        self.sleep.assert_called_once_with(2)

    def test_yandex_captcha_after_navigation_is_denied(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.side_effect = [False, True] * 3
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("challenge appeared")
        with self.access() as access:
            with self.assertRaises(YandexCaptchaDenied):
                access.fetch(YANDEX_URL)
        self.assertEqual(self.page.goto.call_count, 3)
        self.page.content.assert_not_called()
