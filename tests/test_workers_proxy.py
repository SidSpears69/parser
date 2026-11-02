"""Ensure browser and direct marketplace requests share the proxy route."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from web_parser.browser_access import (
    BrowserAccessDenied, BrowserAccessError, PageSnapshot, YandexCaptchaDenied,
    browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.images import ImageBatch
from web_parser.ozon import OzonError
from web_parser.product_links import ProductLinks
from web_parser.ui.workers import BrowserWalkThread


class WorkerProxyTests(unittest.TestCase):
    def test_yandex_captcha_retries_directly_without_changing_other_marketplaces(self):
        links = tuple(ProductLinks(
            str(index), f"https://www.ozon.ru/search/?text={index}",
            f"https://www.wildberries.ru/search/{index}",
            f"https://market.yandex.ru/search?text={index}",
        ) for index in (1, 2))
        upstream_proxy = {"server": "socks5://proxy.example:1080"}
        profiles = []
        visited = []
        image_routes = []
        parsed_routes = []

        class FakeAccess:
            def __init__(self, *_args, profile_dir, proxy, **_options):
                self.profile_dir = profile_dir
                self.direct = profile_dir.name.startswith("web-parser-yandex-direct-")
                self.playwright = object()
                self.effective_proxy = proxy
                self.page = SimpleNamespace(request="direct" if self.direct else "proxy")
                profiles.append((profile_dir, proxy))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def fetch(self, url):
                visited.append((self.direct, url))
                if "market.yandex.ru" in url and not self.direct:
                    raise YandexCaptchaDenied(200, "обнаружена капча Яндекс Маркета")
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            def fetch_wildberries_json(self, url):
                visited.append((self.direct, url))
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

        class FakeImageStore:
            def __init__(self, request_provider, **_options):
                self.request_provider = request_provider

            def save_products(self, _site, _id, marketplace, _products, _url):
                image_routes.append((marketplace, self.request_provider()))
                return ImageBatch((), ())

        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)),
            links, 0, proxy=upstream_proxy,
        )
        recoveries = []
        loaded = []
        worker.yandex_direct_started.connect(recoveries.append)
        worker.page_loaded.connect(lambda *args: loaded.append(args))
        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
            patch("web_parser.ui.workers.ImageStore", FakeImageStore),
            patch("web_parser.ui.workers.parse_ozon_search", return_value=()),
            patch("web_parser.ui.workers.parse_yandex_search", side_effect=lambda current: (
                parsed_routes.append(current.direct) or ()
            )),
        ):
            worker.run()

        self.assertEqual(worker.loaded, 6)
        self.assertEqual(worker.failed, 0)
        self.assertEqual(recoveries, ["1"])
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0][1], upstream_proxy)
        self.assertIsNone(profiles[1][1])
        self.assertFalse(profiles[1][0].exists())
        self.assertEqual(parsed_routes, [True, True])
        self.assertEqual(
            [direct for direct, url in visited if "market.yandex.ru" in url],
            [False, True, True],
        )
        self.assertTrue(all(not direct for direct, url in visited if "market.yandex.ru" not in url))
        self.assertEqual([route for site, route in image_routes if site == "Яндекс Маркет"],
                         ["direct", "direct"])
        self.assertTrue(all(route == "proxy" for site, route in image_routes
                            if site != "Яндекс Маркет"))
        self.assertEqual([row[-1] for row in loaded if row[1] == "Яндекс Маркет"], [True, True])

    def test_ozon_captcha_retries_in_fresh_profile_and_keeps_other_sites_on_saved_profile(self):
        links = tuple(ProductLinks(
            str(index), f"https://www.ozon.ru/search/?text={index}",
            f"https://www.wildberries.ru/search/{index}",
            f"https://market.yandex.ru/search?text={index}",
        ) for index in (1, 2))
        profiles = []
        visited = []

        class FakeAccess:
            def __init__(self, *_args, profile_dir, **_options):
                self.profile_dir = profile_dir
                self.fresh = profile_dir.name.startswith("web-parser-ozon-")
                profiles.append(profile_dir)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def fetch(self, url):
                visited.append((self.fresh, url))
                if "ozon.ru" in url and not self.fresh:
                    raise BrowserAccessDenied(403, "капча Ozon")
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            def fetch_wildberries_json(self, url):
                visited.append((self.fresh, url))
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
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)), links, 0,
        )
        recoveries = []
        worker.ozon_recovery_started.connect(recoveries.append)
        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
            patch("web_parser.ui.workers.parse_ozon_search", return_value=()),
            patch("web_parser.ui.workers.parse_yandex_search", return_value=()),
        ):
            worker.run()

        self.assertEqual(worker.loaded, 6)
        self.assertEqual(worker.failed, 0)
        self.assertEqual(recoveries, ["1"])
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0], browser_profile_dir("sail-master.ru", "Google Chrome"))
        self.assertFalse(profiles[1].exists())
        self.assertEqual(
            [fresh for fresh, url in visited if "ozon.ru" in url],
            [False, True, True],
        )
        self.assertTrue(all(not fresh for fresh, url in visited if "ozon.ru" not in url))

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
