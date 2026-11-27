"""Integration of optional CAPTCHA solving with browser navigation."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserPageAccess, YandexCaptchaDenied,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.captcha_service import CaptchaServiceCancelled, CaptchaServiceError


URL = "https://market.yandex.ru/search?text=rope"


class YandexCaptchaAccessTests(unittest.TestCase):
    def setUp(self):
        self.page = Mock()
        self.page.url = URL
        self.page.content.return_value = "<html>results</html>"
        self.page.evaluate.return_value = False
        self.page.goto.return_value = Mock(status=200, ok=True)
        self.access = BrowserPageAccess(
            BrowserInstallation("Google Chrome", Path(__file__)), 0,
            attempts=3, sleep=Mock(),
        )
        self.access._page = self.page

    @patch.dict("os.environ", {"WEB_PARSER_2CAPTCHA_API_KEY": "test-key"})
    @patch("web_parser.browser_access.page.solve_yandex_captcha", return_value=True)
    def test_success_reloads_and_uses_new_response_status(self, solver):
        self.page.goto.side_effect = [Mock(status=429, ok=False), Mock(status=200, ok=True)]
        self.page.evaluate.side_effect = [True, False, False, False, False]
        result = self.access.fetch(URL)
        self.assertEqual(result.status, 200)
        self.assertEqual(self.page.goto.call_count, 2)
        solver.assert_called_once()

    @patch.dict("os.environ", {"WEB_PARSER_2CAPTCHA_API_KEY": "test-key"})
    @patch("web_parser.browser_access.page.solve_yandex_captcha", return_value=True)
    def test_second_challenge_is_not_solved_repeatedly(self, solver):
        self.page.evaluate.return_value = True
        with self.assertRaises(YandexCaptchaDenied):
            self.access.fetch(URL)
        solver.assert_called_once()
        self.assertEqual(self.page.goto.call_count, 3)

    @patch.dict("os.environ", {"WEB_PARSER_2CAPTCHA_API_KEY": "test-key"})
    @patch("web_parser.browser_access.page.solve_yandex_captcha", side_effect=CaptchaServiceError("ошибка сервиса"))
    def test_service_error_remains_a_captcha_failure(self, solver):
        self.page.evaluate.return_value = True
        with self.assertRaisesRegex(YandexCaptchaDenied, "ошибка сервиса"):
            self.access.fetch(URL)
        self.assertEqual(self.page.goto.call_count, 3)
        solver.assert_called_once()

    @patch.dict("os.environ", {"WEB_PARSER_2CAPTCHA_API_KEY": "test-key"})
    @patch("web_parser.browser_access.page.solve_yandex_captcha", side_effect=CaptchaServiceCancelled())
    def test_cancel_stops_the_walk(self):
        self.page.evaluate.return_value = True
        with self.assertRaises(BrowserAccessCancelled):
            self.access.fetch(URL)

    @patch.dict("os.environ", {"WEB_PARSER_2CAPTCHA_API_KEY": "test-key"})
    @patch("web_parser.browser_access.page.solve_yandex_captcha", return_value=True)
    def test_late_challenge_hands_reload_to_caller(self, solver):
        self.access._last_yandex_url = URL
        self.page.evaluate.side_effect = [True, False, False, False, False]
        with self.assertRaises(YandexCaptchaDenied) as caught:
            self.access.check_yandex_captcha()
        self.assertTrue(caught.exception.solved)
        self.page.goto.assert_not_called()
        solver.assert_called_once()


if __name__ == "__main__":
    unittest.main()
