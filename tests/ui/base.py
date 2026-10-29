"""Shared Qt window setup and asynchronous test helpers."""

import os
from decimal import Decimal
from threading import Event
import time
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from web_parser.app import create_application
from web_parser.ozon import OzonProduct
from web_parser.product_links import ProductLinks
from web_parser.ui import MainWindow
from web_parser.wildberries import WildberriesProduct
from web_parser.yandex_market import YandexMarketProduct


def sample_wildberries_product() -> WildberriesProduct:
    return WildberriesProduct(
        519585970, "https://basket-28.wbbasket.ru/vol5195/part519585/519585970/images/big/1.webp",
        Decimal("3229"), Decimal("930"), "Шнур", Decimal("4.9"), 26,
    )

def sample_ozon_product() -> OzonProduct:
    return OzonProduct(
        "https://ir.ozone.ru/s3/test/photo.jpg", Decimal("1000"), Decimal("750"),
        "Шнур Ozon", Decimal("4.8"), 12,
    )

def sample_yandex_product() -> YandexMarketProduct:
    return YandexMarketProduct(
        "https://avatars.mds.yandex.net/get-mpic/123/photo/orig",
        Decimal("1200"), Decimal("900"), "Шнур Маркет", Decimal("4.7"), 18,
    )


class UiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or create_application([])

    def setUp(self):
        self.window = MainWindow()

        class FakeWildberriesClient:
            def __init__(self, *_args, **_kwargs):
                pass

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

        self._wildberries_patch = patch("web_parser.ui.workers.WildberriesClient", FakeWildberriesClient)
        self._wildberries_patch.start()
        self._ozon_patch = patch(
            "web_parser.ui.workers.parse_ozon_search",
            return_value=(sample_ozon_product(),),
        )
        self._ozon_patch.start()
        self._yandex_patch = patch(
            "web_parser.ui.workers.parse_yandex_search",
            return_value=(sample_yandex_product(),),
        )
        self._yandex_patch.start()

    def tearDown(self):
        self._yandex_patch.stop()
        self._ozon_patch.stop()
        self._wildberries_patch.stop()
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
        page.pause.setValue(1)
        with patch("web_parser.ui.workers.fetch_product_links", return_value=links):
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
