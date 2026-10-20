"""Offline UI checks: python -m unittest discover -s tests -v."""

import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from web_parser.app import create_application
from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError,
    PageSnapshot, browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.product_links import ProductLinks, ProductLinksError
from web_parser.ui import MainWindow, SITES


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _close_window_while_worker_runs(self, start_worker, release: Event) -> None:
        """The window stays responsive until a background worker finishes."""
        self.window.show()
        start_worker()
        started = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(self.window.isVisible())
        release.set()
        deadline = time.monotonic() + 4
        while self.window.isVisible() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.isVisible())

    def _load_links(self, page, links: list[ProductLinks]) -> None:
        page.source_url.setText("https://example.org/products.json")
        page.token.setText("private-token")
        with patch("web_parser.ui.fetch_product_links", return_value=links):
            page.load_links_button.click()
            deadline = time.monotonic() + 3
            while page.links_fetch_pending and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertEqual(page.product_links, tuple(links))

    def _wait_for_walk(self, page) -> None:
        deadline = time.monotonic() + 4
        while page.walk_pending and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(page.walk_pending)

    def _assert_denied_marketplace_is_skipped(
        self, blocked_url_field: str, denial: BrowserAccessDenied,
    ) -> None:
        page = self.window.pages[SITES[0]]
        links = [ProductLinks(
            str(index), f"https://ozon.example/{index}", f"https://wb.example/{index}",
            f"https://market.yandex.ru/search?text={index}",
        ) for index in (1, 2)]
        self._load_links(page, links)
        blocked_url = getattr(links[0], blocked_url_field)
        skipped_url = getattr(links[1], blocked_url_field)
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
                if url == blocked_url:
                    raise denial
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

        with patch("web_parser.ui.BrowserPageAccess", DeniedAccess):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertNotIn(skipped_url, visited)
        self.assertEqual(len(visited), 5)
        self.assertIn("Загружено: 4; ошибок: 1; пропущено: 1", page.run_status.text())
        self.assertEqual(page.log.editor.document().blockCount(), 1)
        log = page.log.editor.toPlainText()
        self.assertIn(str(denial), log)
        self.assertIn(blocked_url, log)

    def test_tabs_keep_independent_values(self):
        self.assertEqual(self.window.tabs.count(), 4)
        self.assertEqual(self.window.tabs.tabText(3), "setepolotna.ru")
        self.assertIn("setepolotna.ru", self.window.pages)
        first, second = (self.window.pages[site] for site in SITES[:2])
        first.source_url.setText("https://example.org/data.json")
        first.ftp_port.setValue(2121)
        first.source_schedule_enabled.setChecked(True)
        self.window.tabs.setCurrentIndex(1)
        second.source_url.setText("https://other.example.org/data.json")
        self.window.tabs.setCurrentIndex(0)
        self.assertEqual(first.settings()["source_url"], "https://example.org/data.json")
        self.assertEqual(first.settings()["ftp"]["port"], 2121)
        self.assertEqual(second.settings()["ftp"]["port"], 21)
        self.assertTrue(first.settings()["source_schedule_enabled"])
        self.assertFalse(second.settings()["source_schedule_enabled"])
        self.assertNotEqual(first.settings()["source_url"], second.settings()["source_url"])

    def test_schedule_toggle_and_secret_fields(self):
        page = self.window.pages[SITES[0]]
        self.assertFalse(page.start_time.isEnabled())
        self.assertFalse(page.source_schedule_enabled.isChecked())
        page.source_schedule_enabled.setChecked(True)
        self.assertTrue(page.settings()["source_schedule_enabled"])
        self.assertFalse(page.start_time.isEnabled())
        page.schedule_enabled.setChecked(True)
        self.assertTrue(page.start_time.isEnabled())
        self.assertTrue(page.interval.isEnabled())
        page.interval.setValue(3)
        page.interval_unit.setCurrentIndex(0)
        self.assertEqual(page.settings()["schedule"]["unit"], "hours")
        self.assertEqual(page.settings()["schedule"]["interval"], 3)
        self.assertEqual(page.token.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(page.ftp_password.echoMode(), QLineEdit.EchoMode.Password)

    def test_clickable_controls_use_hand_cursor_only_when_enabled(self):
        page = self.window.pages[SITES[0]]
        hand = Qt.CursorShape.PointingHandCursor
        arrow = Qt.CursorShape.ArrowCursor
        self.assertEqual(self.window.save_config_button.cursor().shape(), hand)
        self.assertEqual(self.window.tabs.tabBar().cursor().shape(), hand)
        self.assertEqual(page.source_schedule_enabled.cursor().shape(), hand)
        self.assertEqual(page.browser.cursor().shape(), hand)
        self.assertEqual(page.start_button.cursor().shape(), arrow)
        self.assertEqual(page.load_links_button.cursor().shape(), arrow)

        page.source_url.setText("https://example.org/products.json")
        page.token.setText("token")
        self.assertEqual(page.load_links_button.cursor().shape(), hand)
        page.token.clear()
        self.assertEqual(page.load_links_button.cursor().shape(), arrow)

    def test_log_export_clear_and_site_isolation(self):
        first, second = (self.window.pages[site].log for site in SITES[:2])
        first.error_received.emit("42", "https://example.org/product", "Страница не найдена\n404")
        self.app.processEvents()
        self.assertTrue(first.save_button.isEnabled())
        self.assertTrue(second.editor.document().isEmpty())
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "errors.txt"
            first.export_to(output)
            text = output.read_text(encoding="utf-8")
        self.assertIn("ID: 42", text)
        self.assertIn("https://example.org/product", text)
        self.assertIn("Страница не найдена 404", text)
        QTest.mouseClick(first.clear_button, Qt.MouseButton.LeftButton)
        self.assertTrue(first.editor.document().isEmpty())
        self.assertFalse(first.save_button.isEnabled())

    def test_log_limit_and_start_requires_loaded_links(self):
        page = self.window.pages[SITES[0]]
        for index in range(2005):
            page.log.append_error(str(index), "", "Ошибка")
        self.assertEqual(page.log.editor.document().blockCount(), 2000)
        self.assertNotIn("ID: 0 |", page.log.editor.toPlainText())
        QTest.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
        self.assertEqual(page.state_label.text(), "Не запущен")
        self.assertFalse(page.start_button.isEnabled())
        self.assertFalse(page.stop_button.isEnabled())
        self.assertIn("Загрузите ссылки", page.run_status.text())
        self.assertTrue(self.window.save_config_button.isEnabled())

    def test_configuration_round_trip_restores_each_tab(self):
        first = self.window.pages[SITES[0]]
        second = self.window.pages[SITES[1]]
        first.source_url.setText("https://first.example/products.json")
        first.token.setText("secret-token")
        first.source_schedule_enabled.setChecked(True)
        first.browser.setCurrentIndex(2)
        first.pause.setValue(9)
        first.ftp_host.setText("ftp.example.org")
        first.ftp_port.setValue(2121)
        first.ftp_user.setText("user-one")
        first.ftp_password.setText("secret-password")
        first.remote_path.setText("/first/")
        first.schedule_enabled.setChecked(True)
        first.start_time.setTime(first.start_time.time().fromString("08:30", "HH:mm"))
        first.interval.setValue(4)
        first.interval_unit.setCurrentIndex(2)
        second.source_url.setText("https://second.example/products.json")
        expected = {site: page.settings() for site, page in self.window.pages.items()}

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "saved.json"
            self.window.save_config_to(path)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-token", raw)
            self.assertNotIn("secret-password", raw)
            for page in self.window.pages.values():
                page.source_url.clear()
                page.token.clear()
                page.ftp_password.clear()
                page.source_schedule_enabled.setChecked(False)
            self.window.load_config_from(path)

        self.assertEqual(
            {site: page.settings() for site, page in self.window.pages.items()}, expected
        )

    def test_invalid_config_does_not_change_any_tab(self):
        page = self.window.pages[SITES[0]]
        page.source_url.setText("https://current.example/products.json")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.json"
            path.write_text('{"format": "wrong"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.window.load_config_from(path)
        self.assertEqual(page.source_url.text(), "https://current.example/products.json")

    def test_browser_refresh_shows_installations_without_changing_saved_names(self):
        chrome = BrowserInstallation("Google Chrome", Path("C:/Browsers/Chrome/chrome.exe"))
        with patch("web_parser.ui.discover_browsers", return_value={chrome.name: chrome}) as discover:
            self.window.pages[SITES[0]].refresh_browsers_button.click()
        discover.assert_called_once_with()
        for page in self.window.pages.values():
            self.assertIn("не найден", page.browser.itemText(0))
            chrome_index = page.browser.findData("Google Chrome")
            page.browser.setCurrentIndex(chrome_index)
            self.assertIn("найден", page.browser.currentText())
            self.assertIn(str(chrome.path), page.browser_path_label.text())
            self.assertFalse(page.start_button.isEnabled())
            self.assertEqual(page.settings()["browser"], "Google Chrome")

    def test_product_links_load_is_per_site_and_shows_preview(self):
        first, second = (self.window.pages[site] for site in SITES[:2])
        self.assertEqual(first.source_url.text(), "")
        self.assertEqual(second.source_url.text(), "")
        self.assertFalse(first.load_links_button.isEnabled())
        first.source_url.setText("https://example.org/products.json")
        first.token.setText("private-token")
        self.assertTrue(first.load_links_button.isEnabled())
        expected = [
            ProductLinks(
                product_id=str(index),
                ozon_url=f"https://ozon.ru/product/{index}",
                wildberries_url=f"https://wildberries.ru/catalog/{index}/detail.aspx",
                yandex_market_url=f"https://market.yandex.ru/product/{index}",
            )
            for index in range(21)
        ]
        with patch("web_parser.ui.fetch_product_links", return_value=expected) as fetch:
            first.load_links_button.click()
            deadline = time.monotonic() + 3
            while first.links_fetch_pending and time.monotonic() < deadline:
                QTest.qWait(10)

        fetch.assert_called_once_with("https://example.org/products.json", "private-token")
        self.assertEqual(first.product_links, tuple(expected))
        self.assertEqual(second.product_links, ())
        self.assertEqual(first.links_preview.rowCount(), 20)
        def cell_text(column: int) -> str:
            item = first.links_preview.item(0, column)
            if item is None:
                self.fail(f"Нет ячейки в колонке {column}")
            return item.text()

        self.assertEqual(cell_text(0), "0")
        self.assertEqual(cell_text(1), "https://ozon.ru/product/0")
        self.assertEqual(cell_text(2), "https://wildberries.ru/catalog/0/detail.aspx")
        self.assertEqual(cell_text(3), "https://market.yandex.ru/product/0")
        self.assertIn("21", first.links_status.text())
        self.assertIn("первые 20 из 21", first.links_preview_hint.text())
        self.assertIn("не загружены", second.links_status.text())
        first.source_url.setText("https://new.example.org/products.json")
        self.assertEqual(first.product_links, ())
        self.assertEqual(first.links_preview.rowCount(), 0)

    def test_links_preview_uses_narrow_id_and_clickable_full_width_urls(self):
        page = self.window.pages[SITES[0]]
        url = "https://ozon.ru/search/?text=" + "длинная-ссылка-" * 20
        self._load_links(page, [ProductLinks(
            "42", url, "https://wildberries.ru/catalog/42/detail.aspx",
            "https://market.yandex.ru/search?text=42",
        )])
        self.window.show()
        table = page.links_preview
        self.window.tabs.currentWidget().ensureWidgetVisible(table)
        self.app.processEvents()

        self.assertAlmostEqual(table.columnWidth(0), table.viewport().width() // 16, delta=2)
        for column in range(1, 4):
            self.assertGreater(table.columnWidth(column), table.columnWidth(0) * 3)
        self.assertTrue(table.wordWrap())
        self.assertEqual(table.textElideMode(), Qt.TextElideMode.ElideNone)
        self.assertGreater(table.rowHeight(0), table.fontMetrics().height() * 2)
        link_item = table.item(0, 1)
        self.assertFalse(link_item.font().underline())

        link_cell = table.visualItemRect(table.item(0, 1))
        click_near_right_edge = QPoint(link_cell.right() - 3, link_cell.center().y())
        QTest.mouseMove(table.viewport(), pos=link_cell.center())
        self.assertEqual(table.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertTrue(link_item.font().underline())
        self.assertEqual(link_item.foreground().color().name(), "#1b50b6")
        self.assertEqual(link_item.background().color().name(), "#e8f0ff")
        QTest.mouseMove(table.viewport(), pos=table.visualItemRect(table.item(0, 0)).center())
        self.assertEqual(table.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertFalse(link_item.font().underline())
        self.assertEqual(link_item.foreground().color().name(), "#245fd2")
        self.assertEqual(link_item.data(Qt.ItemDataRole.BackgroundRole), None)
        with patch("web_parser.ui.QDesktopServices.openUrl", return_value=True) as open_url:
            QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=click_near_right_edge)
            QTest.mouseClick(
                table.viewport(), Qt.MouseButton.LeftButton,
                pos=table.visualItemRect(table.item(0, 0)).center(),
            )
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0].toString(), url)

    def test_product_links_error_hides_token_and_is_logged(self):
        page = self.window.pages[SITES[0]]
        page.source_url.setText("https://example.org/products.json")
        page.token.setText("secret token+")
        with patch(
            "web_parser.ui.fetch_product_links",
            side_effect=ProductLinksError("Отказано для secret token+ / secret%20token%2B"),
        ):
            page.load_links_button.click()
            deadline = time.monotonic() + 3
            while page.links_fetch_pending and time.monotonic() < deadline:
                QTest.qWait(10)

        self.assertEqual(page.product_links, ())
        self.assertIn("Не удалось загрузить", page.links_status.text())
        combined = page.links_status.text() + page.log.editor.toPlainText()
        self.assertNotIn("secret token+", combined)
        self.assertNotIn("secret%20token%2B", combined)
        self.assertIn("[токен скрыт]", combined)
        self.assertTrue(page.load_links_button.isEnabled())

    def test_product_links_from_previous_source_are_discarded(self):
        page = self.window.pages[SITES[0]]
        page.source_url.setText("https://old.example.org/products.json")
        page.token.setText("old-token")
        release = Event()

        def slow_fetch(_source_url, _token):
            release.wait(3)
            return [ProductLinks(
                "42", "https://ozon.ru/search?q=42",
                "https://wildberries.ru/search?q=42",
                "https://market.yandex.ru/search?q=42",
            )]

        with patch("web_parser.ui.fetch_product_links", side_effect=slow_fetch):
            page.load_links_button.click()
            page.source_url.setText("https://new.example.org/products.json")
            release.set()
            deadline = time.monotonic() + 4
            while page.links_fetch_pending and time.monotonic() < deadline:
                QTest.qWait(10)

        self.assertEqual(page.product_links, ())
        self.assertEqual(page.links_preview.rowCount(), 0)
        self.assertIn("изменились", page.links_status.text())

    def test_window_close_waits_for_product_links_fetch_without_blocking_ui(self):
        page = self.window.pages[SITES[0]]
        page.source_url.setText("https://example.org/products.json")
        page.token.setText("private-token")
        release = Event()

        def slow_fetch(_source_url, _token):
            release.wait(3)
            return [ProductLinks(
                "42", "https://ozon.ru/product/42",
                "https://wildberries.ru/catalog/42/detail.aspx",
                "https://market.yandex.ru/product/42",
            )]

        with patch("web_parser.ui.fetch_product_links", side_effect=slow_fetch):
            self._close_window_while_worker_runs(page.load_links_button.click, release)

    def test_start_visits_all_urls_in_json_order_and_logs_page_error(self):
        page = self.window.pages[SITES[0]]
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
                if url == links[0].wildberries_url:
                    raise BrowserAccessError("HTTP 503")
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

        with patch("web_parser.ui.BrowserPageAccess", FakeAccess):
            page.start_button.click()
            self.assertFalse(page.start_button.isEnabled())
            self.assertFalse(self.window.load_config_button.isEnabled())
            self._wait_for_walk(page)

        self.assertEqual(created[0][0], chrome)
        self.assertEqual(created[0][1], page.pause.value())
        self.assertFalse(created[0][2]["headless"])
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

    def test_resolved_marketplace_challenges_count_as_loaded(self):
        page = self.window.pages[SITES[0]]
        links = [ProductLinks(
            "42", "https://ozon.example/42", "https://wb.example/42", "https://ya.example/42",
        )]
        self._load_links(page, links)

        class ResolvedAccess:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                status = (
                    403 if url == links[0].ozon_url else
                    498 if url == links[0].wildberries_url else 200
                )
                return PageSnapshot(url, url, status, "<html>Товар</html>", 1)

        with patch("web_parser.ui.BrowserPageAccess", ResolvedAccess):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertIn("Загружено: 3; ошибок: 0; пропущено: 0", page.run_status.text())
        self.assertFalse(hasattr(page, "retry_button"))
        self.assertFalse(hasattr(page, "skip_marketplace_button"))

    def test_access_denial_automatically_skips_remaining_marketplace_links(self):
        self._assert_denied_marketplace_is_skipped("ozon_url", BrowserAccessDenied(403))

    def test_yandex_captcha_is_logged_and_only_yandex_links_are_skipped(self):
        self._assert_denied_marketplace_is_skipped(
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

        with patch("web_parser.ui.BrowserPageAccess", FakeAccess):
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

        with patch("web_parser.ui.BrowserPageAccess", FailingAccess):
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

        with patch("web_parser.ui.BrowserPageAccess", SlowAccess):
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

        with patch("web_parser.ui.BrowserPageAccess", SlowAccess):
            self._close_window_while_worker_runs(start_walk, release)
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
