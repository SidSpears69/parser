"""Parsed marketplace result tables and JSON handoff."""

from unittest.mock import patch

from web_parser.browser_access import PageSnapshot
from web_parser.product_links import ProductLinks
from web_parser.ui import SITES

from .base import (
    UiTestCase, sample_ozon_product, sample_wildberries_product, sample_yandex_product,
)


class ResultsTests(UiTestCase):
    def test_resolved_browser_challenge_and_wildberries_json_count_as_loaded(self):
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
                status = 403 if url == links[0].ozon_url else 200
                return PageSnapshot(url, url, status, "<html>Товар</html>", 1)

            @staticmethod
            def fetch_wildberries_json(_url):
                return {"products": [{"id": 519585970}]}

        with patch("web_parser.ui.workers.BrowserPageAccess", ResolvedAccess):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertIn("Загружено: 3; ошибок: 0", page.run_status.text())
        self.assertEqual(page.ozon_table.rowCount(), 1)
        self.assertEqual(page.ozon_table.item(0, 1).text(), "Шнур Ozon")
        self.assertEqual(page.ozon_table.item(0, 2).text(), "1000.00")
        self.assertEqual(page.ozon_table.item(0, 3).text(), "750.00")
        self.assertEqual(page.ozon_table.item(0, 4).text(), "4.8")
        self.assertEqual(page.ozon_table.item(0, 5).text(), "12")
        self.assertEqual(page.ozon_results["42"], (sample_ozon_product(),))
        with patch("web_parser.ui.site.QDesktopServices.openUrl", return_value=True) as open_url:
            page.ozon_table.cellClicked.emit(0, 6)
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0].toString(), sample_ozon_product().image_url)
        self.assertEqual(page.wildberries_table.rowCount(), 1)
        self.assertEqual(page.wildberries_table.item(0, 1).text(), "Шнур")
        self.assertEqual(page.wildberries_table.item(0, 1).toolTip(), "Артикул WB: 519585970")
        self.assertEqual(page.wildberries_table.item(0, 2).text(), "3229.00")
        self.assertEqual(page.wildberries_table.item(0, 3).text(), "930.00")
        self.assertEqual(page.wildberries_table.item(0, 4).text(), "4.9")
        self.assertEqual(page.wildberries_table.item(0, 5).text(), "26")
        self.assertEqual(page.wildberries_results["42"][0].wb_id, 519585970)
        with patch("web_parser.ui.site.QDesktopServices.openUrl", return_value=True) as open_url:
            page.wildberries_table.cellClicked.emit(0, 6)
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0].toString(), sample_wildberries_product().image_url)
        self.assertEqual(page.yandex_table.rowCount(), 1)
        self.assertEqual(page.yandex_table.item(0, 1).text(), "Шнур Маркет")
        self.assertEqual(page.yandex_table.item(0, 2).text(), "1200.00")
        self.assertEqual(page.yandex_table.item(0, 3).text(), "900.00")
        self.assertEqual(page.yandex_table.item(0, 4).text(), "4.7")
        self.assertEqual(page.yandex_table.item(0, 5).text(), "18")
        self.assertEqual(page.yandex_table.horizontalHeaderItem(5).text(), "Оценки")
        self.assertEqual(page.yandex_results["42"], (sample_yandex_product(),))
        with patch("web_parser.ui.site.QDesktopServices.openUrl", return_value=True) as open_url:
            page.yandex_table.cellClicked.emit(0, 6)
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0].toString(), sample_yandex_product().image_url)
        self.assertFalse(hasattr(page, "retry_button"))
        self.assertFalse(hasattr(page, "skip_marketplace_button"))

    def test_wildberries_browser_json_is_parsed_without_direct_search_request(self):
        page = self.window.pages[SITES[0]]
        links = [ProductLinks(
            "42", "https://ozon.example/42",
            "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
            "https://ya.example/42",
        )]
        self._load_links(page, links)
        captured = {"products": [{"id": 519585970}]}
        visited = []

        class FakeAccess:
            def __init__(self, *_args, **_kwargs):
                pass

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
                return captured

        class BrowserJsonClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(_url):
                raise AssertionError("Прямой запрос поиска не должен предшествовать браузеру")

            @staticmethod
            def parse_document(document):
                if document != captured:
                    raise AssertionError("Получен чужой JSON")
                return (sample_wildberries_product(),)

        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", BrowserJsonClient),
        ):
            page.start_button.click()
            self._wait_for_walk(page)
        self.assertEqual(visited, [
            links[0].ozon_url, links[0].wildberries_url, links[0].yandex_market_url,
        ])
        self.assertEqual(page.wildberries_table.rowCount(), 1)
        self.assertIn("Загружено: 3; ошибок: 0", page.run_status.text())
