"""Ensure browser and direct marketplace requests share the proxy route."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from web_parser.browser_access import (
    BrowserAccessDenied, BrowserAccessError, BrowserCaptchaDenied, BrowserFetchFailed,
    BrowserProxyAuthenticationRequired, BrowserProxyConnectionFailed, BrowserSessionClosed,
    PageSnapshot, YandexCaptchaDenied)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.images import ImageBatch
from web_parser.ozon import OzonError
from web_parser.product_links import ProductLinks
from web_parser.proxy_pool import ProxyPool
from web_parser.ui.workers import BrowserWalkThread


class MarketplaceRoutesTests(unittest.TestCase):
    def setUp(self):
        self.first_proxy = "http://first.example:8080"
        self.second_proxy = "socks5://second.example:1080"
        self.local_relay = {"server": "socks5://127.0.0.1:34567"}
        self.slots = (
            {"server": self.first_proxy, "username": "", "password": ""},
            {"server": self.second_proxy, "username": "proxy-user", "password": "proxy-pass"},
        )
        self.pool = ProxyPool()
        self.links = tuple(ProductLinks(
            str(index), f"https://www.ozon.ru/search/?text={index}",
            f"https://www.wildberries.ru/search/{index}",
            f"https://market.yandex.ru/search?text={index}",
        ) for index in (1, 2))
        self.visited = []
        self.attempt_budgets = []
        self.profiles = []
        self.image_routes = []
        self.wildberries_requests = []
        self.failures = {}

        test = self

        class FakeAccess:
            def __init__(self, *_args, profile_dir, proxy, playwright=None, **_options):
                self.route = proxy["server"] if proxy else None
                self.effective_proxy = test.local_relay if self.route == test.second_proxy else proxy
                self.playwright = playwright if playwright is not None else object()
                self.page = SimpleNamespace(request=self.route)
                test.profiles.append((profile_dir, self.route, self.playwright))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def fetch(self, url, *, attempts=None):
                test.visited.append((url, self.route))
                test.attempt_budgets.append((url, self.route, attempts))
                error = test.failures.get((url, self.route))
                if error is not None:
                    raise error
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            def fetch_wildberries_json(self, url):
                self.fetch(url)
                return {"products": []}

        class FakeWildberriesClient:
            def __init__(self, *, proxy, **_options):
                self.proxy = proxy

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def fetch(self, url):
                test.wildberries_requests.append((url, self.proxy))
                return ()

            @staticmethod
            def parse_document(_document):
                return ()

        class FakeImageStore:
            def __init__(self, request_provider, **_options):
                self.request_provider = request_provider

            def save_products(self, _site, product_id, marketplace, _products, _url):
                test.image_routes.append((product_id, marketplace, self.request_provider()))
                return ImageBatch((), ())

        for patcher in (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient),
            patch("web_parser.ui.workers.ImageStore", FakeImageStore),
            patch("web_parser.ui.workers.parse_ozon_search", return_value=()),
            patch("web_parser.ui.workers.parse_yandex_search", return_value=()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _walk(self, links=None, *, ftp_settings=None):
        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)),
            self.links if links is None else links, 0, headless=True,
            proxy_routes=self.pool.routes_for_walk(self.slots),
            ftp_settings=ftp_settings,
        )
        worker.run()
        return worker

    def test_proxy_connection_failure_keeps_working_route_for_one_marketplace(self):
        for product in self.links:
            self.failures[product.ozon_url, self.first_proxy] = BrowserProxyConnectionFailed("Прокси недоступен")

        worker = self._walk()

        self.assertEqual((worker.loaded, worker.failed), (6, 0))
        successful_visits = [
            (url, route) for product in self.links for url, route in (
                (product.ozon_url, self.second_proxy),
                (product.wildberries_url, self.first_proxy),
                (product.yandex_market_url, self.first_proxy),
            )
        ]
        self.assertEqual(self.visited, [(self.links[0].ozon_url, self.first_proxy), *successful_visits])
        self.assertEqual(self.image_routes, [
            (product.product_id, marketplace, route) for product in self.links
            for marketplace, route in (
                ("Ozon", self.second_proxy), ("Wildberries", self.first_proxy),
                ("Яндекс Маркет", self.first_proxy),
            )
        ])
        first, second = self.profiles
        self.assertNotEqual(first[0], second[0])
        self.assertIs(first[2], second[2])

        self.visited.clear()
        restarted = self._walk()

        self.assertEqual((restarted.loaded, restarted.failed), (6, 0))
        self.assertEqual(self.visited, successful_visits)

    def test_wildberries_http_and_images_use_selected_browser_relay(self):
        product = self.links[0]
        self.failures[product.wildberries_url, self.first_proxy] = BrowserProxyConnectionFailed("Прокси недоступен")
        self.failures[product.wildberries_url, self.second_proxy] = BrowserAccessError("JSON не получен")

        worker = self._walk((product,))

        self.assertEqual((worker.loaded, worker.failed), (3, 0))
        self.assertEqual(self.wildberries_requests, [(product.wildberries_url, self.local_relay)])
        self.assertIn((product.product_id, "Wildberries", self.second_proxy), self.image_routes)

    def test_ozon_tries_third_proxy_after_connection_failure_and_http_407(self):
        http_slot, socks_slot = self.slots
        third_proxy = "http://third.example:8080"
        self.slots = (
            socks_slot, http_slot,
            {"server": third_proxy, "username": "third-user", "password": "third-pass"},
        )
        first, second = self.links
        self.failures[first.ozon_url, socks_slot["server"]] = BrowserProxyConnectionFailed("Прокси недоступен")
        self.failures[first.ozon_url, http_slot["server"]] = BrowserProxyAuthenticationRequired()
        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)), self.links, 0,
            proxy_routes=self.pool.routes_for_walk(self.slots),
        )
        notices = []
        worker.browser_notice.connect(notices.append)

        worker.run()

        self.assertEqual((worker.loaded, worker.failed), (6, 0))
        self.assertEqual([visit for visit in self.visited if visit[0] in (first.ozon_url, second.ozon_url)], [
            (first.ozon_url, socks_slot["server"]), (first.ozon_url, http_slot["server"]),
            (first.ozon_url, third_proxy), (second.ozon_url, third_proxy),
        ])
        self.assertIn((first.product_id, "Ozon", third_proxy), self.image_routes)
        self.assertEqual(len(notices), 1)
        self.assertIn("прокси №2", notices[0])
        self.assertIn("HTTP 407", notices[0])

    def test_failed_direct_request_ends_route_sequence(self):
        product = self.links[0]
        for route in (self.first_proxy, self.second_proxy):
            self.failures[product.ozon_url, route] = BrowserProxyConnectionFailed("Прокси недоступен")
        self.failures[product.ozon_url, None] = BrowserFetchFailed("Страница недоступна")

        worker = self._walk((product,))

        self.assertEqual((worker.loaded, worker.failed), (2, 1))
        self.assertEqual(self.visited, [
            (product.ozon_url, self.first_proxy),
            (product.ozon_url, self.second_proxy),
            (product.ozon_url, None),
            (product.wildberries_url, self.first_proxy),
            (product.yandex_market_url, self.first_proxy),
        ])

    def test_empty_response_reports_each_attempted_route(self):
        product = self.links[0]
        self.failures[product.ozon_url, self.first_proxy] = BrowserProxyConnectionFailed("Прокси недоступен")
        self.failures[product.ozon_url, self.second_proxy] = BrowserFetchFailed("ERR_EMPTY_RESPONSE")
        failures = []
        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)), (product,), 0,
            proxy_routes=self.pool.routes_for_walk(self.slots),
        )
        worker.page_failed.connect(lambda *args: failures.append(args))

        worker.run()

        self.assertEqual([route for url, route in self.visited if url == product.ozon_url],
                         [self.first_proxy, self.second_proxy])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][-1], "Маршрут: прокси №1 (HTTP) → прокси №2 (SOCKS5). ERR_EMPTY_RESPONSE")

    def test_closed_browser_stops_walk_without_attempting_ftp(self):
        first_url = self.links[0].ozon_url
        self.failures[first_url, self.first_proxy] = BrowserSessionClosed("Браузер или его контекст закрыт.")

        with patch("web_parser.ui.workers.export_run") as export:
            worker = self._walk(ftp_settings={"host": "ftp.example"})

        self.assertEqual(self.visited, [(first_url, self.first_proxy)])
        self.assertIn("Браузер или его контекст закрыт.", worker.error or "")
        self.assertIn("прокси №1 (HTTP)", worker.error or "")
        self.assertIsNone(worker.ftp_result)
        export.assert_not_called()

    def test_direct_route_is_remembered_after_proxy_connection_failures(self):
        product = self.links[0]
        for route in (self.first_proxy, self.second_proxy):
            self.failures[product.ozon_url, route] = BrowserProxyConnectionFailed("Прокси недоступен")

        worker = self._walk((product,))

        self.assertEqual((worker.loaded, worker.failed), (3, 0))
        self.assertEqual(self.visited[:3], [
            (product.ozon_url, self.first_proxy),
            (product.ozon_url, self.second_proxy),
            (product.ozon_url, None),
        ])
        self.assertIn((product.product_id, "Ozon", None), self.image_routes)
        self.visited.clear()

        restarted = self._walk((product,))

        self.assertEqual((restarted.loaded, restarted.failed), (3, 0))
        self.assertEqual(self.visited, [
            (product.ozon_url, None),
            (product.wildberries_url, self.first_proxy),
            (product.yandex_market_url, self.first_proxy),
        ])

    def test_access_requirements_keep_the_selected_route(self):
        product = self.links[0]
        for status in (401, 403, 498):
            with self.subTest(status=status):
                self.failures[product.ozon_url, self.first_proxy] = BrowserAccessDenied(status)
                self.visited.clear()

                worker = self._walk((product,))

                self.assertEqual((worker.loaded, worker.failed), (2, 1))
                self.assertEqual(self.visited, [
                    (product.ozon_url, self.first_proxy),
                    (product.wildberries_url, self.first_proxy),
                    (product.yandex_market_url, self.first_proxy),
                ])

    def test_rate_limit_and_captcha_rotate_per_marketplace_and_remember_success(self):
        for attribute, error in (
            ("ozon_url", BrowserAccessDenied(429)),
            ("wildberries_url", BrowserCaptchaDenied(200)),
        ):
            with self.subTest(marketplace=attribute):
                self.pool = ProxyPool()
                self.failures.clear()
                self.visited.clear()
                url = getattr(self.links[0], attribute)
                self.failures[url, self.first_proxy] = error

                worker = self._walk()

                self.assertEqual((worker.loaded, worker.failed), (6, 0))
                self.assertIn((url, self.first_proxy), self.visited)
                self.assertIn((url, self.second_proxy), self.visited)
                self.assertIn((getattr(self.links[1], attribute), self.second_proxy), self.visited)
                self.assertNotIn((getattr(self.links[1], attribute), self.first_proxy), self.visited)
                self.visited.clear()
                self._walk((self.links[0],))
                self.assertIn((url, self.second_proxy), self.visited)
                self.assertNotIn((url, self.first_proxy), self.visited)

    def test_all_blocked_routes_fail_only_current_product(self):
        for route in (self.first_proxy, self.second_proxy, None):
            self.failures[self.links[0].ozon_url, route] = BrowserAccessDenied(429)

        worker = self._walk()

        self.assertEqual((worker.loaded, worker.failed), (5, 1))
        self.assertEqual([route for url, route in self.visited if url == self.links[0].ozon_url],
                         [self.first_proxy, self.second_proxy, None])
        self.assertIn((self.links[1].ozon_url, self.first_proxy), self.visited)

    def test_each_product_can_retry_route_that_failed_for_previous_product(self):
        first, second = self.links
        self.failures[first.ozon_url, self.first_proxy] = BrowserAccessDenied(429)
        self.failures[second.ozon_url, self.second_proxy] = BrowserAccessDenied(429)

        worker = self._walk()

        self.assertEqual((worker.loaded, worker.failed), (6, 0))
        self.assertEqual([route for url, route in self.visited if url == second.ozon_url],
                         [self.second_proxy, self.first_proxy])

    def test_yandex_captcha_tries_all_routes_and_reports_failure_once(self):
        first, second = self.links
        for route in (self.first_proxy, self.second_proxy, None):
            # fetch raises only after exhausting its own CAPTCHA attempts.
            self.failures[first.yandex_market_url, route] = YandexCaptchaDenied(200, "капча")
        worker = BrowserWalkThread(
            "sail-master.ru", BrowserInstallation("Google Chrome", Path(__file__)), self.links, 0,
            proxy_routes=self.pool.routes_for_walk(self.slots),
            ftp_settings={"host": "ftp.example"},
        )
        failures = []
        worker.page_failed.connect(lambda *args: failures.append(args))
        with (
            patch("web_parser.ui.workers.parse_ozon_search", return_value=(object(),)),
            patch("web_parser.ui.workers.export_run") as export,
        ):
            worker.run()

        self.assertEqual((worker.loaded, worker.failed), (5, 1))
        self.assertEqual(
            [route for url, route in self.visited if url == first.yandex_market_url],
            [self.first_proxy, self.second_proxy, None],
        )
        self.assertEqual(
            [(route, attempts) for url, route, attempts in self.attempt_budgets if url == first.yandex_market_url],
            [(self.first_proxy, 3), (self.second_proxy, 3), (None, 3)],
        )
        self.assertTrue(any(url == second.yandex_market_url for url, _ in self.visited))
        self.assertEqual(len(failures), 1)
        self.assertIn("капч", failures[0][-1].lower())
        self.assertIn("Загружено страниц: 5 из 6", worker.ftp_skip_reason or "")
        export.assert_not_called()

    def test_yandex_captcha_switches_to_next_proxy_or_direct(self):
        product = self.links[0]
        for failing_routes, expected_routes in (
            ((self.first_proxy,), [self.first_proxy, self.second_proxy]),
            ((self.first_proxy, self.second_proxy), [self.first_proxy, self.second_proxy, None]),
        ):
            with self.subTest(failing_routes=failing_routes):
                self.pool = ProxyPool()
                self.failures.clear()
                self.visited.clear()
                for route in failing_routes:
                    self.failures[product.yandex_market_url, route] = YandexCaptchaDenied(200)

                worker = self._walk((product,))

                self.assertEqual((worker.loaded, worker.failed), (3, 0))
                self.assertEqual(
                    [route for url, route in self.visited if url == product.yandex_market_url],
                    expected_routes,
                )
                self.assertIn((product.product_id, "Яндекс Маркет", expected_routes[-1]), self.image_routes)

    def test_yandex_direct_route_is_last_even_if_previously_preferred(self):
        product = self.links[0]
        self.pool.routes_for_walk(self.slots).remember("Яндекс Маркет", 0)
        for route in (self.first_proxy, self.second_proxy):
            self.failures[product.yandex_market_url, route] = YandexCaptchaDenied(200)

        worker = self._walk((product,))

        self.assertEqual((worker.loaded, worker.failed), (3, 0))
        self.assertEqual(
            [route for url, route in self.visited if url == product.yandex_market_url],
            [self.first_proxy, self.second_proxy, None],
        )

    def test_yandex_captcha_during_parsing_retries_then_switches_route(self):
        captcha = YandexCaptchaDenied(None)
        with patch(
            "web_parser.ui.workers.parse_yandex_search", side_effect=(captcha, captcha, captcha, (), ()),
        ) as parse:
            worker = self._walk()

        self.assertEqual((worker.loaded, worker.failed), (6, 0))
        self.assertIsNone(worker.fatal_layout)
        self.assertEqual(parse.call_count, 5)
        url = self.links[0].yandex_market_url
        self.assertEqual(
            [(route, attempts) for visited_url, route, attempts in self.attempt_budgets if visited_url == url],
            [(self.first_proxy, 3), (self.first_proxy, 2), (self.first_proxy, 1), (self.second_proxy, 3)],
        )
        self.assertIn((self.links[1].yandex_market_url, self.second_proxy), self.visited)

    def test_rate_limits_fall_back_to_direct_connection(self):
        product = self.links[0]
        for route in (self.first_proxy, self.second_proxy):
            self.failures[product.ozon_url, route] = BrowserAccessDenied(429)

        worker = self._walk((product,))

        self.assertEqual((worker.loaded, worker.failed), (3, 0))
        self.assertEqual([route for url, route in self.visited if url == product.ozon_url],
                         [self.first_proxy, self.second_proxy, None])


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
            def fetch(url, **_kwargs):
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
            def fetch(url, **_kwargs):
                return PageSnapshot(url, url, 200, "<html>ok</html>", 1)

            @staticmethod
            def fetch_wildberries_json(_url):
                raise BrowserAccessError("JSON не получен")

        class FakeWildberriesClient:
            def __init__(self, *, proxy, playwright, **_options):
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
            def fetch(url, **_kwargs):
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
