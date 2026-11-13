"""Scheduled source downloads and browser walks on independent site tabs."""

import time
from threading import Event
from unittest.mock import patch

from PySide6.QtCore import QTime
from PySide6.QtTest import QTest

from web_parser.browser_access import PageSnapshot
from web_parser.product_links import ProductLinks
from web_parser.ui import SITES

from .base import UiTestCase


LINKS = [ProductLinks(
    "1473", "https://ozon.example/search", "https://wb.example/search",
    "https://market.example/search",
)]


class RecordingAccess:
    visited: list[str] = []
    created_options: list[dict] = []

    def __init__(self, *_args, **options):
        self.created_options.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    @classmethod
    def fetch(cls, url):
        cls.visited.append(url)
        return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

    @classmethod
    def fetch_wildberries_json(cls, url):
        cls.visited.append(url)
        return {"products": []}


class SchedulerTests(UiTestCase):
    def test_source_schedule_loads_only_its_tab_and_advances_once(self):
        first, second = (self.window.pages[site] for site in SITES[:2])
        first.source_url.setText("https://example.org/products.json")
        first.token.setText("token")
        first.source_schedule_time.setTime(QTime(7, 15))
        first.source_schedule_enabled.setChecked(True)
        due = first.scheduler.next_source
        assert due is not None
        with patch("web_parser.ui.workers.fetch_product_links", return_value=LINKS) as fetch:
            first.scheduler.tick(due)
            deadline = time.monotonic() + 3
            while first.links_fetch_pending and time.monotonic() < deadline:
                QTest.qWait(10)
            first.scheduler.tick(due)
        self.assertEqual(first.product_links, tuple(LINKS))
        self.assertEqual(second.product_links, ())
        self.assertEqual(fetch.call_count, 1)
        self.assertGreater(first.scheduler.next_source, due)
        self.assertIn("Следующая загрузка:", first.source_next_label.text())

    def test_simultaneous_schedules_load_links_then_walk_once(self):
        page = self.window.pages[SITES[0]]
        page.source_url.setText("https://example.org/products.json")
        page.token.setText("token")
        page.source_schedule_time.setTime(QTime(8, 30))
        page.start_time.setTime(QTime(8, 30))
        page.source_schedule_enabled.setChecked(True)
        page.schedule_enabled.setChecked(True)
        source_due = page.scheduler.next_source
        analysis_due = page.scheduler.next_analysis
        assert source_due is not None and analysis_due is not None
        due = max(source_due, analysis_due)
        RecordingAccess.visited = []
        RecordingAccess.created_options = []

        with (
            patch("web_parser.ui.workers.fetch_product_links", return_value=LINKS) as fetch,
            patch("web_parser.ui.workers.BrowserPageAccess", RecordingAccess),
        ):
            page.scheduler.tick(due)
            deadline = time.monotonic() + 4
            while (page.links_fetch_pending or page.walk_pending) and time.monotonic() < deadline:
                QTest.qWait(10)
            page.scheduler.tick(due)
        self.assertFalse(page.links_fetch_pending)
        self.assertFalse(page.walk_pending)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(RecordingAccess.visited, [
            LINKS[0].ozon_url, LINKS[0].wildberries_url, LINKS[0].yandex_market_url,
        ])
        self.assertTrue(RecordingAccess.created_options)
        self.assertTrue(all(not options["headless"] for options in RecordingAccess.created_options))
        self.assertEqual(self.window.pages[SITES[1]].product_links, ())
        self.assertIn("Загружено: 3", page.run_status.text())

    def test_analysis_schedule_bootstraps_links_without_source_schedule(self):
        page = self.window.pages[SITES[0]]
        page.source_url.setText("https://example.org/products.json")
        page.token.setText("token")
        page.schedule_enabled.setChecked(True)
        due = page.scheduler.next_analysis
        assert due is not None
        RecordingAccess.visited = []
        RecordingAccess.created_options = []
        with (
            patch("web_parser.ui.workers.fetch_product_links", return_value=LINKS) as fetch,
            patch("web_parser.ui.workers.BrowserPageAccess", RecordingAccess),
        ):
            page.scheduler.tick(due)
            deadline = time.monotonic() + 4
            while (page.links_fetch_pending or page.walk_pending) and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(RecordingAccess.visited), 3)
        self.assertTrue(RecordingAccess.created_options)
        self.assertTrue(all(not options["headless"] for options in RecordingAccess.created_options))
        self.assertIn("Загружено: 3", page.run_status.text())

    def test_schedule_reconfigures_when_time_changes(self):
        page = self.window.pages[SITES[0]]
        page.schedule_enabled.setChecked(True)
        old = page.scheduler.next_analysis
        page.start_time.setTime(QTime(9, 45))
        self.assertIsNotNone(page.scheduler.next_analysis)
        self.assertEqual(page.scheduler.next_analysis.hour, 9)
        self.assertEqual(page.scheduler.next_analysis.minute, 45)
        self.assertNotEqual(page.scheduler.next_analysis, old)
        page.schedule_enabled.setChecked(False)
        self.assertIsNone(page.scheduler.next_analysis)
        self.assertEqual(page.analysis_next_label.text(), "Следующий анализ: выключен.")

    def test_due_analysis_during_active_walk_is_skipped(self):
        page = self.window.pages[SITES[0]]
        self._load_links(page, LINKS)
        page.schedule_enabled.setChecked(True)
        due = page.scheduler.next_analysis
        assert due is not None
        started = Event()
        release = Event()

        class BlockingAccess(RecordingAccess):
            @classmethod
            def fetch(cls, url):
                started.set()
                release.wait(3)
                return super().fetch(url)

        with patch("web_parser.ui.workers.BrowserPageAccess", BlockingAccess):
            try:
                page.scheduler.tick(due)
                deadline = time.monotonic() + 3
                while not started.is_set() and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertTrue(started.is_set())
                next_occurrence = page.scheduler.next_analysis
                assert next_occurrence is not None
                page.scheduler.tick(next_occurrence)
                self.assertIn("предыдущий обход ещё идёт", page.log.editor.toPlainText())
            finally:
                release.set()
            self._wait_for_walk(page)
