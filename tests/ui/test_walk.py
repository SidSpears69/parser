"""Browser walk, denial, cancellation and window shutdown."""

from pathlib import Path
from threading import Event
import time
from unittest.mock import patch

from PySide6.QtTest import QTest

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError,
    PageSnapshot, browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.product_links import ProductLinks
from web_parser.ui import SITES

from .base import UiTestCase, sample_wildberries_product


class WalkTests(UiTestCase):
    def _assert_denied_marketplace_links_are_tried_individually(
        self, url_field: str, denial: BrowserAccessDenied,
    ) -> None:
        page = self.window.pages[SITES[0]]
        links = [ProductLinks(
            str(index), f"https://ozon.example/{index}", f"https://wb.example/{index}",
            f"https://market.yandex.ru/search?text={index}",
        ) for index in (1, 2)]
        self._load_links(page, links)
        denied_urls = {getattr(product, url_field) for product in links}
        visited = []

        class DeniedAccess:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                visited.append(url)
                if url in denied_urls:
                    raise denial
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

            @staticmethod
            def fetch_wildberries_json(url):
                visited.append(url)
                if url in denied_urls:
                    raise denial
                return {"products": [{"id": 519585970}]}

        class TrackedWildberriesClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                visited.append(url)
                if url in denied_urls:
                    raise denial
                return (sample_wildberries_product(),)

            @staticmethod
            def parse_document(_document):
                return (sample_wildberries_product(),)

        with (
            patch("web_parser.ui.workers.BrowserPageAccess", DeniedAccess),
            patch("web_parser.ui.workers.WildberriesClient", TrackedWildberriesClient),
        ):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertEqual(visited, [
            links[0].ozon_url, links[0].wildberries_url, links[0].yandex_market_url,
            links[1].ozon_url, links[1].wildberries_url, links[1].yandex_market_url,
        ])
        self.assertIn("Загружено: 4; ошибок: 2", page.run_status.text())
        self.assertEqual(page.log.editor.document().blockCount(), 2)
        log = page.log.editor.toPlainText()
        self.assertEqual(log.count(str(denial)), 2)
        for product in links:
            self.assertIn(getattr(product, url_field), log)
        self.assertNotIn("пропущены", log)

    def test_start_visits_all_urls_in_json_order_and_logs_page_error(self):
        page = self.window.pages[SITES[0]]
        self.window.proxy_enabled.setChecked(True)
        self.window.proxy_server.setText("http://ru-proxy.example:8080")
        self.window.proxy_username.setText("proxy-user")
        self.window.proxy_password.setText("proxy-secret")
        chrome = BrowserInstallation("Google Chrome", Path("C:/Browsers/Chrome/chrome.exe"))
        page.set_available_browsers({chrome.name: chrome})
        page.browser.setCurrentIndex(page.browser.findData(chrome.name))
        links = [ProductLinks(
            str(index), f"https://ozon.example/{index}",
            f"https://wildberries.example/{index}", f"https://market.example/{index}",
        ) for index in (1, 2)]
        self._load_links(page, links)
        self.assertTrue(page.start_button.isEnabled())
        self.assertFalse(hasattr(page, "launch_browser_button"))
        self.assertFalse(hasattr(page, "close_browser_button"))
        visited = []
        created = []

        class FakeAccess:
            def __init__(self, installation, pause_seconds, **options):
                created.append((installation, pause_seconds, options))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                visited.append(url)
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

            @staticmethod
            def fetch_wildberries_json(url):
                visited.append(url)
                if url == links[0].wildberries_url:
                    raise BrowserAccessDenied(503)
                return {"products": [{"id": 519585970}]}

        class FakeWildberriesClient:
            def __init__(self, *, proxy, **_kwargs):
                self.proxy = proxy

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(_url):
                return (sample_wildberries_product(),)

            @staticmethod
            def parse_document(_document):
                return (sample_wildberries_product(),)

        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
        ):
            page.start_button.click()
            self.assertFalse(page.start_button.isEnabled())
            self.assertFalse(self.window.load_config_button.isEnabled())
            self._wait_for_walk(page)

        self.assertEqual(created[0][0], chrome)
        self.assertEqual(created[0][1], page.pause.value())
        self.assertFalse(created[0][2]["headless"])
        self.assertEqual(created[0][2]["proxy"], {
            "server": "http://ru-proxy.example:8080",
            "username": "proxy-user", "password": "proxy-secret",
        })
        self.assertEqual(created[0][2]["profile_dir"], browser_profile_dir(page.site, chrome.name))
        self.assertEqual(visited, [
            links[0].ozon_url, links[0].wildberries_url, links[0].yandex_market_url,
            links[1].ozon_url, links[1].wildberries_url, links[1].yandex_market_url,
        ])
        self.assertIn("Загружено: 5; ошибок: 1", page.run_status.text())
        self.assertEqual(page.state_label.text(), "Завершён")
        self.assertIn("HTTP 503", page.log.editor.toPlainText())
        self.assertTrue(page.start_button.isEnabled())
        self.assertTrue(self.window.load_config_button.isEnabled())

    def test_long_walk_url_wraps_without_expanding_the_window(self):
        page = self.window.pages[SITES[0]]
        long_url = "https://ozon.example/search/?text=" + "x" * 600
        chrome = BrowserInstallation(
            "Google Chrome", Path("C:/Browsers/" + "x" * 400 + "/chrome.exe"),
        )
        page.set_available_browsers({chrome.name: chrome})
        page.browser.setCurrentIndex(page.browser.findData(chrome.name))
        self._load_links(page, [ProductLinks(
            "42", long_url, "https://wb.example/42", "https://market.example/42",
        )])
        self.window.resize(1440, 900)
        self.window.show()
        self.app.processEvents()
        scroll = self.window.tabs.currentWidget()
        browser_panel = page.run_status.parentWidget()
        initial_panel_width = browser_panel.width()
        initial_horizontal_scroll = scroll.horizontalScrollBar().maximum()
        self.assertLess(initial_horizontal_scroll, 300)
        self.assertGreater(
            page.browser_path_label.height(), page.browser_path_label.fontMetrics().height() * 2,
        )
        release = Event()
        entered = Event()

        class PausedAccess:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                if url == long_url:
                    entered.set()
                    release.wait(3)
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

            @staticmethod
            def fetch_wildberries_json(_url):
                return {"products": [{"id": 519585970}]}

        try:
            with patch("web_parser.ui.workers.BrowserPageAccess", PausedAccess):
                page.start_button.click()
                deadline = time.monotonic() + 3
                while (
                    not entered.is_set() or "Открывается Ozon" not in page.run_status.text()
                ) and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertTrue(entered.is_set())
                self.assertIn("Открывается Ozon", page.run_status.text())
                self.app.processEvents()
                self.assertEqual(page.run_status.text().replace("\u200b", "").split(": ", 1)[1], long_url)
                self.assertGreater(page.run_status.height(), page.run_status.fontMetrics().height() * 3)
                self.assertEqual(browser_panel.width(), initial_panel_width)
                self.assertLessEqual(scroll.horizontalScrollBar().maximum(), initial_horizontal_scroll)
                self.assertEqual(self.window.width(), 1440)
        finally:
            release.set()
            self._wait_for_walk(page)

    def test_ozon_denial_is_logged_for_each_link_without_skipping(self):
        self._assert_denied_marketplace_links_are_tried_individually(
            "ozon_url", BrowserAccessDenied(403),
        )

    def test_wildberries_denial_is_logged_for_each_link_without_skipping(self):
        self._assert_denied_marketplace_links_are_tried_individually(
            "wildberries_url", BrowserAccessDenied(498),
        )

    def test_yandex_captcha_is_logged_for_each_link_without_skipping(self):
        self._assert_denied_marketplace_links_are_tried_individually(
            "yandex_market_url",
            BrowserAccessDenied(200, "обнаружена капча Яндекс Маркета; страница недоступна."),
        )

    def test_firefox_start_uses_playwright_build_without_stock_installation(self):
        page = self.window.pages[SITES[0]]
        page.set_available_browsers({})
        page.browser.setCurrentIndex(page.browser.findData("Mozilla Firefox"))
        self._load_links(page, [ProductLinks(
            "42", "https://ozon.example/42", "https://wb.example/42", "https://ya.example/42",
        )])
        selected = []

        class FakeAccess:
            def __init__(self, installation, *_args, **_kwargs):
                selected.append(installation)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

            @staticmethod
            def fetch_wildberries_json(_url):
                return {"products": [{"id": 519585970}]}

        with patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertEqual(selected[0].name, "Mozilla Firefox")
        self.assertEqual(page.state_label.text(), "Завершён")

    def test_startup_error_is_shown_and_logged(self):
        page = self.window.pages[SITES[0]]
        self._load_links(page, [ProductLinks(
            "42", "https://ozon.example/42", "https://wb.example/42", "https://ya.example/42",
        )])

        class FailingAccess:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                raise BrowserAccessError("Сборка Firefox не установлена")

            def __exit__(self, *_args):
                pass

        with patch("web_parser.ui.workers.BrowserPageAccess", FailingAccess):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertIn("Сборка Firefox", page.run_status.text())
        self.assertIn("Сборка Firefox", page.log.editor.toPlainText())
        self.assertTrue(page.start_button.isEnabled())

    def test_stop_cancels_walk_and_closes_browser_session(self):
        page = self.window.pages[SITES[0]]
        self._load_links(page, [ProductLinks(
            "42", "https://ozon.example/42", "https://wb.example/42", "https://ya.example/42",
        )])
        entered, release = Event(), Event()
        visited = []
        closed = []

        class SlowAccess:
            def __init__(self, _installation, _pause, **options):
                self.cancel_requested = options["cancel_requested"]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                closed.append(True)

            def fetch(self, url):
                visited.append(url)
                entered.set()
                release.wait(3)
                if self.cancel_requested():
                    raise BrowserAccessCancelled("Остановлено")
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

        with patch("web_parser.ui.workers.BrowserPageAccess", SlowAccess):
            page.start_button.click()
            self.assertTrue(entered.wait(2))
            page.stop_button.click()
            self.assertFalse(page.stop_button.isEnabled())
            release.set()
            self._wait_for_walk(page)
        self.assertEqual(visited, [page.product_links[0].ozon_url])
        self.assertEqual(closed, [True])
        self.assertEqual(page.state_label.text(), "Остановлен")

    def test_window_close_during_walk_does_not_block_ui(self):
        page = self.window.pages[SITES[0]]
        self._load_links(page, [ProductLinks(
            "42", "https://ozon.example/42", "https://wb.example/42", "https://ya.example/42",
        )])
        entered, release = Event(), Event()
        closed = []

        class SlowAccess:
            def __init__(self, _installation, _pause, **options):
                self.cancel_requested = options["cancel_requested"]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                closed.append(True)

            def fetch(self, url):
                entered.set()
                release.wait(3)
                if self.cancel_requested():
                    raise BrowserAccessCancelled("Остановлено")
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

        def start_walk():
            page.start_button.click()
            self.assertTrue(entered.wait(2))

        with patch("web_parser.ui.workers.BrowserPageAccess", SlowAccess):
            self._close_window_while_worker_runs(start_walk, release)
        self.assertEqual(closed, [True])
