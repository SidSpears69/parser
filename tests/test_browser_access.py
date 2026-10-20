"""Offline checks for Playwright page access, delays and bounded retries."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError,
    BrowserPageAccess, browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation


BINARY = Path(__file__)
CHROME = BrowserInstallation("Google Chrome", BINARY)
URL = "https://market.example/search?q=rope"
OZON_URL = "https://www.ozon.ru/search/?text=rope"
WILDBERRIES_URL = "https://www.wildberries.ru/catalog/0/search.aspx?search=rope"
YANDEX_URL = "https://market.yandex.ru/search?text=rope"


class BrowserAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = Mock()
        self.playwright_patcher = patch("web_parser.browser_access.sync_playwright")
        self.stealth_patcher = patch("web_parser.browser_access.Stealth")
        self.start_playwright = self.playwright_patcher.start()
        self.stealth = self.stealth_patcher.start()
        self.addCleanup(self.playwright_patcher.stop)
        self.addCleanup(self.stealth_patcher.stop)
        self.playwright = self.start_playwright.return_value.start.return_value
        self.browser = self.playwright.chromium.launch.return_value
        self.context = self.browser.new_context.return_value
        self.page = self.context.new_page.return_value
        self.response = Mock(ok=True, status=200)
        self.page.goto.return_value = self.response
        self.page.url = URL
        self.page.content.return_value = "<html><body>Товар</body></html>"
        self.page.evaluate.return_value = False

    def access(self, attempts: int = 3) -> BrowserPageAccess:
        return BrowserPageAccess(CHROME, 2, attempts=attempts, sleep=self.sleep)

    def test_selected_chromium_binary_uses_minimal_browser_flag(self):
        with self.access() as access:
            result = access.fetch(URL)
        self.playwright.chromium.launch.assert_called_once_with(
            executable_path=str(BINARY), headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.browser.new_context.assert_called_once_with(locale="ru-RU")
        self.stealth.assert_not_called()
        self.context.new_page.assert_called_once_with()
        self.assertEqual(result.status, 200)
        self.assertEqual(result.html, "<html><body>Товар</body></html>")
        self.assertEqual(result.attempts, 1)
        self.page.goto.assert_called_once_with(URL, wait_until="load", timeout=30_000)
        self.sleep.assert_not_called()
        self.context.close.assert_called_once_with()
        self.browser.close.assert_called_once_with()
        self.playwright.stop.assert_called_once_with()

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

    def test_yandex_captcha_with_http_200_is_denied_without_retry(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.return_value = True
        with self.access() as access:
            with self.assertRaisesRegex(BrowserAccessDenied, "HTTP 200: обнаружена капча"):
                access.fetch(YANDEX_URL)
        self.page.goto.assert_called_once()
        self.page.content.assert_not_called()
        self.sleep.assert_not_called()

    def test_yandex_captcha_redirect_is_denied(self):
        self.page.url = "https://yandex.ru/showcaptcha?retpath=market.yandex.ru"
        with self.access() as access:
            with self.assertRaisesRegex(BrowserAccessDenied, "капча"):
                access.fetch(YANDEX_URL)
        self.page.evaluate.assert_not_called()
        self.page.goto.assert_called_once()

    def test_yandex_search_requires_results_before_success(self):
        self.page.url = YANDEX_URL
        with self.access() as access:
            result = access.fetch(YANDEX_URL)
        self.assertEqual(result.status, 200)
        self.assertIn("searchOrganic", self.page.wait_for_function.call_args.args[0])
        self.assertEqual(self.page.wait_for_function.call_args.kwargs["timeout"], 10_000)
        self.page.wait_for_timeout.assert_called_once_with(750)
        self.assertEqual(self.page.evaluate.call_count, 2)

    def test_yandex_captcha_after_navigation_is_denied(self):
        self.page.url = YANDEX_URL
        self.page.evaluate.side_effect = [False, True]
        self.page.wait_for_function.side_effect = PlaywrightTimeoutError("challenge appeared")
        with self.access() as access:
            with self.assertRaisesRegex(BrowserAccessDenied, "капча"):
                access.fetch(YANDEX_URL)
        self.page.goto.assert_called_once()
        self.page.content.assert_not_called()

    def test_dedicated_persistent_profile_is_used_for_chromium(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "browser-profile"
            context = self.playwright.chromium.launch_persistent_context.return_value
            context.pages = [self.page]
            with BrowserPageAccess(CHROME, 2, profile_dir=profile) as access:
                result = access.fetch(URL)
            self.assertTrue(profile.is_dir())
        self.playwright.chromium.launch.assert_not_called()
        self.playwright.chromium.launch_persistent_context.assert_called_once_with(
            user_data_dir=str(profile), executable_path=str(BINARY),
            headless=False, locale="ru-RU",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.stealth.assert_not_called()
        context.new_page.assert_not_called()
        context.close.assert_called_once_with()
        self.assertEqual(result.status, 200)

    def test_profile_paths_are_separate_by_site_and_browser(self):
        first = browser_profile_dir("sail-master.ru", "Mozilla Firefox")
        second = browser_profile_dir("fishingline.ru", "Mozilla Firefox")
        third = browser_profile_dir("sail-master.ru", "Google Chrome")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(first.name, "firefox")

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

    def test_firefox_uses_playwright_build_and_firefox_safe_stealth(self):
        firefox = BrowserInstallation("Mozilla Firefox", BINARY.with_name("missing_stock_firefox.exe"))
        self.playwright.firefox.executable_path = str(BINARY)
        firefox_browser = self.playwright.firefox.launch.return_value
        firefox_context = firefox_browser.new_context.return_value
        with BrowserPageAccess(firefox, 1):
            pass
        self.playwright.firefox.launch.assert_called_once_with(headless=False)
        self.playwright.chromium.launch.assert_not_called()
        firefox_browser.new_context.assert_called_once_with(locale="ru-RU")
        self.stealth.return_value.apply_stealth_sync.assert_called_once_with(firefox_context)
        options = self.stealth.call_args.kwargs
        self.assertFalse(options["chrome_app"])
        self.assertFalse(options["navigator_user_agent"])
        self.assertFalse(options["webgl_vendor"])
        self.assertTrue(options["init_scripts_only"])
        firefox_context.close.assert_called_once_with()
        firefox_browser.close.assert_called_once_with()
        self.playwright.stop.assert_called_once_with()

    def test_missing_playwright_firefox_build_has_install_hint(self):
        firefox = BrowserInstallation("Mozilla Firefox", BINARY)
        self.playwright.firefox.executable_path = str(BINARY.with_name("missing_firefox_build.exe"))
        with self.assertRaisesRegex(BrowserAccessError, "playwright install firefox"):
            with BrowserPageAccess(firefox, 1):
                pass
        self.playwright.firefox.launch.assert_not_called()
        self.playwright.stop.assert_called_once_with()

    def test_failed_firefox_stealth_setup_closes_partial_session(self):
        firefox = BrowserInstallation("Mozilla Firefox", BINARY)
        self.playwright.firefox.executable_path = str(BINARY)
        firefox_browser = self.playwright.firefox.launch.return_value
        firefox_context = firefox_browser.new_context.return_value
        self.stealth.return_value.apply_stealth_sync.side_effect = PlaywrightError("setup failed")
        with self.assertRaisesRegex(BrowserAccessError, "setup failed"):
            with BrowserPageAccess(firefox, 1):
                pass
        firefox_context.close.assert_called_once_with()
        firefox_browser.close.assert_called_once_with()
        self.playwright.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
