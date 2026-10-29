"""Ensure browser and direct marketplace requests share the proxy route."""

from pathlib import Path
import unittest
from unittest.mock import patch

from web_parser.browser_access import BrowserAccessError, PageSnapshot
from web_parser.browser_discovery import BrowserInstallation
from web_parser.ozon import OzonError
from web_parser.product_links import ProductLinks
from web_parser.ui.workers import BrowserWalkThread


class WorkerProxyTests(unittest.TestCase):
    def test_ozon_parse_error_is_logged_and_next_link_is_visited(self):
        links = tuple(ProductLinks(
            str(index), f"https://ozon.example/{index}",
            f"https://wildberries.example/{index}", f"https://market.example/{index}",
        ) for index in (1, 2))
        visited = []
        failures = []

        class FakeAccess:
            def __init__(self, *_args, **_options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                visited.append(url)
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            @staticmethod
            def fetch_wildberries_json(url):
                visited.append(url)
                return {"products": []}

        class FakeWildberriesClient:
            def __init__(self, **_options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def parse_document(_document):
                return ()

        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)),
            links, 0,
        )
        worker.page_failed.connect(lambda *args: failures.append(args))
        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
            patch("web_parser.ui.workers.parse_ozon_search", side_effect=(
                OzonError("нет карточек"), (),
            )),
            patch("web_parser.ui.workers.parse_yandex_search", return_value=()),
        ):
            worker.run()

        self.assertEqual(worker.loaded, 5)
        self.assertEqual(worker.failed, 1)
        self.assertEqual(failures[0][0:2], ("1", "Ozon"))
        self.assertEqual(visited[-1], links[1].yandex_market_url)

    def test_wildberries_request_uses_browser_relay(self):
        upstream = {
            "server": "socks5://upstream.example:63397",
            "username": "user", "password": "password",
        }
        local = {"server": "socks5://127.0.0.1:34567"}
        playwright_instance = object()
        client_options = []

        class FakeAccess:
            def __init__(self, *_args, **_options):
                self.effective_proxy = local
                self.playwright = playwright_instance

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            @staticmethod
            def fetch_wildberries_json(_url):
                return {"products": []}

        class FakeWildberriesClient:
            def __init__(self, *, proxy, playwright):
                client_options.append((proxy, playwright))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(_url):
                return ()

            @staticmethod
            def parse_document(_document):
                return ()

        links = (ProductLinks(
            "42", "https://ozon.example/42", "https://wildberries.example/42",
            "https://market.example/42",
        ),)
        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)),
            links, 0, proxy=upstream,
        )
        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
            patch("web_parser.ui.workers.parse_ozon_search", return_value=()),
            patch("web_parser.ui.workers.parse_yandex_search", return_value=()),
        ):
            worker.run()

        self.assertEqual(client_options, [(local, playwright_instance)])
        self.assertEqual(worker.loaded, 3)
        self.assertEqual(worker.failed, 0)

    def test_direct_search_is_used_after_browser_json_error(self):
        links = (ProductLinks(
            "42", "https://ozon.example/42", "https://wildberries.example/42",
            "https://market.example/42",
        ),)
        direct_requests = []

        class FakeAccess:
            def __init__(self, *_args, **_options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            @staticmethod
            def fetch_wildberries_json(_url):
                raise BrowserAccessError("JSON не получен")

        class FakeWildberriesClient:
            def __init__(self, **_options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url):
                direct_requests.append(url)
                return ()

        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)), links, 0,
        )
        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
            patch("web_parser.ui.workers.parse_ozon_search", return_value=()),
            patch("web_parser.ui.workers.parse_yandex_search", return_value=()),
        ):
            worker.run()

        self.assertEqual(direct_requests, [links[0].wildberries_url])
        self.assertEqual(worker.loaded, 3)
        self.assertEqual(worker.failed, 0)


if __name__ == "__main__":
    unittest.main()
