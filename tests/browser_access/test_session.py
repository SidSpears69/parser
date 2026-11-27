"""Browser startup, shutdown, and proxy configuration tests."""

from pathlib import Path
import socket
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import BrowserAccessError, BrowserPageAccess
from web_parser.browser_discovery import BrowserInstallation
from web_parser.config import BROWSERS

from .base import BINARY, CHROME, URL, BrowserAccessTestCase


class BrowserSessionTests(BrowserAccessTestCase):
    def test_selected_chromium_binary_uses_context_stealth_without_custom_flags(self):
        with self.access() as access:
            result = access.fetch(URL)
        self.playwright.chromium.launch.assert_called_once_with(
            executable_path=str(BINARY), headless=False,
            chromium_sandbox=True,
        )
        self.browser.new_context.assert_called_once_with(locale="ru-RU")
        self.stealth.return_value.apply_stealth_sync.assert_called_once_with(self.context)
        self.context.new_page.assert_called_once_with()
        self.assertEqual(result.status, 200)
        self.assertEqual(result.html, "<html><body>Товар</body></html>")
        self.assertEqual(result.attempts, 1)
        self.page.goto.assert_called_once_with(URL, wait_until="load", timeout=30_000)
        self.sleep.assert_not_called()
        self.context.close.assert_called_once_with()
        self.browser.close.assert_called_once_with()
        self.playwright.stop.assert_called_once_with()

    def test_second_browser_session_reuses_playwright_without_stopping_the_owner(self):
        with BrowserPageAccess(CHROME, 2, playwright=self.playwright) as access:
            self.assertIs(access.playwright, self.playwright)
        self.start_playwright.assert_not_called()
        self.context.close.assert_called_once_with()
        self.browser.close.assert_called_once_with()
        self.playwright.stop.assert_not_called()

    def test_authenticated_proxy_is_passed_to_chromium_launch(self):
        proxy = {
            "server": "http://ru-proxy.example:8080",
            "username": "user", "password": "secret",
        }
        with BrowserPageAccess(CHROME, 2, proxy=proxy):
            pass
        self.playwright.chromium.launch.assert_called_once_with(
            executable_path=str(BINARY), headless=False,
            chromium_sandbox=True, proxy=proxy,
        )

    def test_authenticated_socks5_proxy_uses_and_closes_loopback_relay(self):
        proxy = {
            "server": "socks5://ru-proxy.example:63397",
            "username": "user", "password": "secret",
        }
        access = BrowserPageAccess(CHROME, 2, proxy=proxy)
        with access:
            passed_proxy = self.playwright.chromium.launch.call_args.kwargs["proxy"]
            self.assertEqual(access.effective_proxy, passed_proxy)
            self.assertEqual(
                self.playwright.chromium.launch.call_args.kwargs["ignore_default_args"],
                ['--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"'],
            )
            parsed = urlsplit(passed_proxy["server"])
            self.assertEqual(parsed.scheme, "socks5")
            self.assertEqual(parsed.hostname, "127.0.0.1")
            self.assertIsNotNone(parsed.port)
            self.assertNotIn("username", passed_proxy)
            self.assertNotIn("password", passed_proxy)
            with socket.create_connection((parsed.hostname, parsed.port), timeout=1) as connection:
                connection.sendall(bytes((5, 1, 0)))
                self.assertEqual(connection.recv(2), bytes((5, 0)))
        self.assertIsNone(access._proxy_relay)
        with self.assertRaises(OSError):
            socket.create_connection((parsed.hostname, parsed.port), timeout=0.2)

    def test_all_chromium_browsers_filter_unsupported_socks5_resolver_flag(self):
        proxy = {
            "server": "socks5://ru-proxy.example:63397",
            "username": "user", "password": "secret",
        }
        for name in BROWSERS[:-1]:
            with self.subTest(browser=name), tempfile.TemporaryDirectory() as folder:
                installation = BrowserInstallation(name, BINARY)
                context = self.playwright.chromium.launch_persistent_context.return_value
                context.pages = [self.page]
                with BrowserPageAccess(installation, 1, profile_dir=Path(folder), proxy=proxy):
                    pass
                launch_options = self.playwright.chromium.launch_persistent_context.call_args.kwargs
                local_proxy = urlsplit(launch_options["proxy"]["server"])
                self.assertEqual(local_proxy.scheme, "socks5")
                self.assertEqual(local_proxy.hostname, "127.0.0.1")
                self.assertEqual(
                    launch_options["ignore_default_args"],
                    ['--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"'],
                )
                self.assertTrue(launch_options["chromium_sandbox"])
                self.playwright.chromium.launch_persistent_context.reset_mock()

    def test_firefox_socks5_launch_does_not_receive_chromium_options(self):
        firefox = BrowserInstallation("Mozilla Firefox", BINARY)
        self.playwright.firefox.executable_path = str(BINARY)
        proxy = {"server": "socks5://ru-proxy.example:1080"}
        with BrowserPageAccess(firefox, 1, proxy=proxy):
            pass
        self.playwright.firefox.launch.assert_called_once_with(
            headless=False, proxy=proxy,
        )

    def test_proxy_credentials_are_hidden_in_launch_errors(self):
        proxy = {
            "server": "http://ru-proxy.example:8080",
            "username": "proxy-user", "password": "secret+password",
        }
        self.playwright.chromium.launch.side_effect = PlaywrightError(
            "Proxy rejected proxy-user / secret+password / secret%2Bpassword"
        )
        with self.assertRaises(BrowserAccessError) as caught:
            with BrowserPageAccess(CHROME, 2, proxy=proxy):
                pass
        self.assertNotIn("proxy-user", str(caught.exception))
        self.assertNotIn("secret+password", str(caught.exception))
        self.assertNotIn("secret%2Bpassword", str(caught.exception))

    def test_browser_launch_timeout_has_a_short_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.playwright.chromium.launch_persistent_context.side_effect = (
                PlaywrightTimeoutError("long Playwright call log")
            )
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            with self.assertRaisesRegex(BrowserAccessError, "не ответил за 20 секунд") as caught:
                with BrowserPageAccess(installation, 0, profile_dir=Path(folder)):
                    pass
        self.assertNotIn("long Playwright call log", str(caught.exception))
        self.playwright.stop.assert_called_once_with()

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

    def test_missing_packaged_firefox_build_has_installer_hint(self):
        firefox = BrowserInstallation("Mozilla Firefox", BINARY)
        self.playwright.firefox.executable_path = str(BINARY.with_name("missing_firefox_build.exe"))
        with patch("web_parser.browser_access.session.sys.frozen", True, create=True):
            with self.assertRaisesRegex(BrowserAccessError, "Переустановите Web Parser"):
                with BrowserPageAccess(firefox, 1):
                    pass
        self.playwright.firefox.launch.assert_not_called()

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
