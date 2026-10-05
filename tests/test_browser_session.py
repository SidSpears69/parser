"""Browser session checks without opening a real browser or using the network."""

from pathlib import Path
import unittest
from unittest.mock import patch

from selenium.common.exceptions import WebDriverException

from web_parser.browser_session import BrowserError, BrowserSessionManager


BINARY = Path(__file__)


class BrowserSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = BrowserSessionManager()

    def test_chrome_uses_installed_binary_and_selenium_driver_manager(self):
        with patch("web_parser.browser_session.webdriver.Chrome") as chrome:
            chrome.return_value.capabilities = {"browserVersion": "140.0"}
            chrome.return_value.service.path = "cached/chromedriver.exe"
            description = self.session.launch("Google Chrome", BINARY)
        self.assertTrue(self.session.is_running)
        self.assertIn("140.0", description)
        self.assertIn("chromedriver.exe", description)
        self.assertEqual(chrome.call_args.kwargs["options"].binary_location, str(BINARY))
        self.assertNotIn("service", chrome.call_args.kwargs)
        self.session.close()
        chrome.return_value.quit.assert_called_once_with()
        self.assertFalse(self.session.is_running)

    def test_edge_and_firefox_use_own_webdriver(self):
        for name, factory in (("Microsoft Edge", "Edge"), ("Mozilla Firefox", "Firefox")):
            with self.subTest(name=name), patch(f"web_parser.browser_session.webdriver.{factory}") as start:
                start.return_value.capabilities = {}
                start.return_value.service.path = "driver.exe"
                self.session.launch(name, BINARY)
                self.assertEqual(start.call_args.kwargs["options"].binary_location, str(BINARY))
                self.session.close()

    def test_yandex_uses_matching_official_driver(self):
        with (patch("web_parser.browser_session.browser_file_version", return_value="26.8.0.1234"),
              patch("web_parser.browser_session.ensure_yandex_driver", return_value=Path("yandexdriver.exe")) as ensure,
              patch("web_parser.browser_session.webdriver.Chrome") as start):
            start.return_value.capabilities = {"browserVersion": "26.8.0"}
            start.return_value.service.path = "yandexdriver.exe"
            description = self.session.launch("Яндекс Браузер", BINARY)
            ensure.assert_called_once_with("26.8.0.1234")
            self.assertIn("26.8.0.1234", description)
            self.assertEqual(start.call_args.kwargs["options"].binary_location, str(BINARY))
            self.assertEqual(start.call_args.kwargs["service"].path, "yandexdriver.exe")

    def test_missing_binary_and_duplicate_launch_do_not_start_session(self):
        with self.assertRaisesRegex(BrowserError, "не найден"):
            self.session.launch("Google Chrome", BINARY.with_name("missing-browser.exe"))
        with patch("web_parser.browser_session.webdriver.Chrome") as start:
            start.return_value.capabilities = {}
            start.return_value.service.path = "driver.exe"
            self.session.launch("Google Chrome", BINARY)
            with self.assertRaisesRegex(BrowserError, "уже запущен"):
                self.session.launch("Google Chrome", BINARY)
            start.assert_called_once()

    def test_webdriver_error_is_reported_and_session_stays_closed(self):
        with patch("web_parser.browser_session.webdriver.Chrome", side_effect=WebDriverException("bad driver")):
            with self.assertRaisesRegex(BrowserError, "bad driver"):
                self.session.launch("Google Chrome", BINARY)
        self.assertFalse(self.session.is_running)


if __name__ == "__main__":
    unittest.main()
