"""Offline UI checks: python -m unittest discover -s tests -v."""

import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from web_parser.app import create_application
from web_parser.browser_discovery import BrowserInstallation
from web_parser.browser_session import BrowserError
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

    def test_tabs_keep_independent_values(self):
        self.assertEqual(self.window.tabs.count(), 4)
        first, second = (self.window.pages[site] for site in SITES[:2])
        first.source_url.setText("https://example.org/data.json")
        first.ftp_port.setValue(2121)
        self.window.tabs.setCurrentIndex(1)
        second.source_url.setText("https://other.example.org/data.json")
        self.window.tabs.setCurrentIndex(0)
        self.assertEqual(first.settings()["source_url"], "https://example.org/data.json")
        self.assertEqual(first.settings()["ftp"]["port"], 2121)
        self.assertEqual(second.settings()["ftp"]["port"], 21)
        self.assertNotEqual(first.settings()["source_url"], second.settings()["source_url"])

    def test_schedule_toggle_and_secret_fields(self):
        page = self.window.pages[SITES[0]]
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

    def test_log_limit_and_launch_placeholder(self):
        page = self.window.pages[SITES[0]]
        for index in range(2005):
            page.log.append_error(str(index), "", "Ошибка")
        self.assertEqual(page.log.editor.document().blockCount(), 2000)
        self.assertNotIn("ID: 0 |", page.log.editor.toPlainText())
        QTest.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
        self.assertIn("Запуск не выполнен", self.window.statusBar().currentMessage())
        self.assertEqual(page.state_label.text(), "Не запущен")
        self.assertFalse(page.stop_button.isEnabled())
        self.assertTrue(self.window.save_config_button.isEnabled())

    def test_configuration_round_trip_restores_each_tab(self):
        first = self.window.pages[SITES[0]]
        second = self.window.pages[SITES[1]]
        first.source_url.setText("https://first.example/products.json")
        first.token.setText("secret-token")
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
            self.assertTrue(page.launch_browser_button.isEnabled())
            self.assertEqual(page.settings()["browser"], "Google Chrome")

    def test_browser_launch_and_close_uses_selected_binary(self):
        chrome = BrowserInstallation("Google Chrome", Path("C:/Browsers/Chrome/chrome.exe"))
        page = self.window.pages[SITES[0]]
        page.set_available_browsers({chrome.name: chrome})
        page.browser.setCurrentIndex(page.browser.findData(chrome.name))

        class FakeManager:
            def __init__(self):
                self.is_running = False
                self.launched_with = None
                self.closed = False

            def launch(self, name, binary):
                self.launched_with = (name, binary)
                self.is_running = True
                return "Chrome 100; драйвер: mock-driver.exe"

            def close(self):
                self.is_running = False
                self.closed = True

        fake_manager = FakeManager()
        with patch("web_parser.ui.BrowserSessionManager", return_value=fake_manager):
            page.launch_browser_button.click()
            deadline = time.monotonic() + 3
            while page.browser_launch_pending and time.monotonic() < deadline:
                QTest.qWait(10)

        self.assertFalse(page.browser_launch_pending)
        self.assertEqual(fake_manager.launched_with, (chrome.name, chrome.path))
        self.assertIn("Браузер запущен", page.browser_launch_status.text())
        self.assertFalse(page.browser.isEnabled())
        self.assertTrue(page.close_browser_button.isEnabled())
        page.close_browser_button.click()
        self.assertTrue(fake_manager.closed)
        self.assertTrue(page.browser.isEnabled())
        self.assertTrue(page.launch_browser_button.isEnabled())
        self.assertIn("Браузер закрыт", page.browser_launch_status.text())

    def test_browser_launch_error_is_shown_and_logged(self):
        chrome = BrowserInstallation("Google Chrome", Path("C:/Browsers/Chrome/chrome.exe"))
        page = self.window.pages[SITES[0]]
        page.set_available_browsers({chrome.name: chrome})
        page.browser.setCurrentIndex(page.browser.findData(chrome.name))

        class FailingManager:
            is_running = False

            def launch(self, _name, _binary):
                raise BrowserError("Драйвер недоступен")

            def close(self):
                pass

        with patch("web_parser.ui.BrowserSessionManager", return_value=FailingManager()):
            page.launch_browser_button.click()
            deadline = time.monotonic() + 3
            while page.browser_launch_pending and time.monotonic() < deadline:
                QTest.qWait(10)

        self.assertIn("Драйвер недоступен", page.browser_launch_status.text())
        self.assertIn("Драйвер недоступен", page.log.editor.toPlainText())
        self.assertTrue(page.launch_browser_button.isEnabled())

    def test_window_close_during_driver_lookup_does_not_block_ui(self):
        chrome = BrowserInstallation("Google Chrome", Path("C:/Browsers/Chrome/chrome.exe"))
        page = self.window.pages[SITES[0]]
        page.set_available_browsers({chrome.name: chrome})
        page.browser.setCurrentIndex(page.browser.findData(chrome.name))
        release = Event()

        class SlowManager:
            def __init__(self):
                self.is_running = False
                self.closed = False

            def launch(self, _name, _binary):
                release.wait(3)
                self.is_running = True
                return "mock driver"

            def close(self):
                self.is_running = False
                self.closed = True

        slow_manager = SlowManager()
        self.window.show()
        with patch("web_parser.ui.BrowserSessionManager", return_value=slow_manager):
            page.launch_browser_button.click()
            started = time.monotonic()
            self.window.close()
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(self.window.isVisible())
            release.set()
            deadline = time.monotonic() + 4
            while self.window.isVisible() and time.monotonic() < deadline:
                QTest.qWait(10)

        self.assertFalse(self.window.isVisible())
        self.assertTrue(slow_manager.closed)


if __name__ == "__main__":
    unittest.main()
