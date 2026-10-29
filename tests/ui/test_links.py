"""Source JSON loading and the link preview table."""

from threading import Event
import time
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from web_parser.product_links import ProductLinks, ProductLinksError
from web_parser.ui import SITES

from .base import UiTestCase


class LinksTests(UiTestCase):
    def test_product_links_load_is_per_site_and_shows_preview(self):
        first, second = (self.window.pages[site] for site in SITES[:2])
        self.assertIn("ещё не было", first.last_load_label.text())
        self.assertIn("ещё не было", second.last_load_label.text())
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
        with patch("web_parser.ui.workers.fetch_product_links", return_value=expected) as fetch:
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
        self.assertRegex(
            first.last_load_label.text(),
            r"Последняя успешная загрузка: \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}",
        )
        self.assertIn("ещё не было", second.last_load_label.text())
        self.assertIn("первые 20 из 21", first.links_preview_hint.text())
        self.assertIn("не загружены", second.links_status.text())
        first.source_url.setText("https://new.example.org/products.json")
        self.assertEqual(first.product_links, ())
        self.assertEqual(first.links_preview.rowCount(), 0)

    def test_last_successful_load_survives_failed_retry(self):
        page = self.window.pages[SITES[0]]
        self._load_links(page, [])
        last_load = page.last_load_label.text()
        self.assertNotIn("ещё не было", last_load)

        with patch(
            "web_parser.ui.workers.fetch_product_links",
            side_effect=ProductLinksError("Временная ошибка"),
        ):
            page.load_links_button.click()
            deadline = time.monotonic() + 3
            while page.links_fetch_pending and time.monotonic() < deadline:
                QTest.qWait(10)

        self.assertIn("Не удалось загрузить", page.links_status.text())
        self.assertEqual(page.last_load_label.text(), last_load)

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
        with patch("web_parser.ui.site.QDesktopServices.openUrl", return_value=True) as open_url:
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
            "web_parser.ui.workers.fetch_product_links",
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

        with patch("web_parser.ui.workers.fetch_product_links", side_effect=slow_fetch):
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

        with patch("web_parser.ui.workers.fetch_product_links", side_effect=slow_fetch):
            self._close_window_while_worker_runs(page.load_links_button.click, release)
