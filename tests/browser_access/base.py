"""Shared Playwright fixture for browser access tests."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from web_parser.browser_access import BrowserPageAccess
from web_parser.browser_discovery import BrowserInstallation


BINARY = Path(__file__)
CHROME = BrowserInstallation("Google Chrome", BINARY)
URL = "https://market.example/search?q=rope"
OZON_URL = "https://www.ozon.ru/search/?text=rope"
WILDBERRIES_URL = "https://www.wildberries.ru/catalog/0/search.aspx?search=rope"
WILDBERRIES_BRAND_URL = (
    "https://www.wildberries.ru/brands/312181720-sailmaster"
    "?sort=popular&page=1&f14177449=11807341"
)
YANDEX_URL = "https://market.yandex.ru/search?text=rope"


class BrowserAccessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = Mock()
        self.enterContext(patch.dict("os.environ", {"WEB_PARSER_2CAPTCHA_API_KEY": ""}))
        self.playwright_patcher = patch("web_parser.browser_access.session.sync_playwright")
        self.stealth_patcher = patch("web_parser.browser_access.session.Stealth")
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

    def access(self, attempts: int = 3, **options) -> BrowserPageAccess:
        return BrowserPageAccess(CHROME, 2, attempts=attempts, sleep=self.sleep, **options)
