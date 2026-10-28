"""Ensure browser and direct marketplace requests share the proxy route."""

from pathlib import Path
import unittest
from unittest.mock import patch

from web_parser.browser_access import PageSnapshot
from web_parser.browser_discovery import BrowserInstallation
from web_parser.product_links import ProductLinks
from web_parser.ui.workers import BrowserWalkThread


class WorkerProxyTests(unittest.TestCase):
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
        ):
            worker.run()

        self.assertEqual(client_options, [(local, playwright_instance)])
        self.assertEqual(worker.loaded, 3)
        self.assertEqual(worker.failed, 0)


if __name__ == "__main__":
    unittest.main()
