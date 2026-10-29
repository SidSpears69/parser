"""Settings, scheduling, proxy and browser controls."""

from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import QLineEdit

from web_parser.browser_discovery import BrowserInstallation
from web_parser.product_links import ProductLinks
from web_parser.ui import SITES

from .base import UiTestCase


class SettingsTests(UiTestCase):
    def test_tabs_keep_independent_values(self):
        self.assertEqual(self.window.tabs.count(), 4)
        self.assertEqual(self.window.tabs.tabText(3), "setepolotna.ru")
        self.assertIn("setepolotna.ru", self.window.pages)
        first, second = (self.window.pages[site] for site in SITES[:2])
        first.source_url.setText("https://example.org/data.json")
        first.ftp_port.setValue(2121)
        first.source_schedule_enabled.setChecked(True)
        first.source_schedule_time.setTime(QTime(6, 15))
        first.source_schedule_interval.setValue(2)
        first.source_schedule_unit.setCurrentIndex(2)
        self.window.tabs.setCurrentIndex(1)
        second.source_url.setText("https://other.example.org/data.json")
        second.source_schedule_time.setTime(QTime(7, 40))
        second.source_schedule_interval.setValue(4)
        self.window.tabs.setCurrentIndex(0)
        self.assertEqual(first.settings()["source_url"], "https://example.org/data.json")
        self.assertEqual(first.settings()["ftp"]["port"], 2121)
        self.assertEqual(second.settings()["ftp"]["port"], 21)
        self.assertTrue(first.settings()["source_schedule_enabled"])
        self.assertFalse(second.settings()["source_schedule_enabled"])
        self.assertEqual(first.settings()["source_schedule_time"], "06:15")
        self.assertEqual(second.settings()["source_schedule_time"], "07:40")
        self.assertEqual(first.settings()["source_schedule_interval"], 2)
        self.assertEqual(first.settings()["source_schedule_unit"], "weeks")
        self.assertEqual(second.settings()["source_schedule_interval"], 4)
        self.assertNotEqual(first.settings()["source_url"], second.settings()["source_url"])

    def test_schedule_toggle_and_secret_fields(self):
        page = self.window.pages[SITES[0]]
        self.assertFalse(page.start_time.isEnabled())
        self.assertFalse(page.source_schedule_time.isEnabled())
        self.assertFalse(page.source_schedule_interval.isEnabled())
        self.assertFalse(page.source_schedule_unit.isEnabled())
        self.assertFalse(page.source_schedule_enabled.isChecked())
        page.source_schedule_enabled.setChecked(True)
        self.assertTrue(page.settings()["source_schedule_enabled"])
        self.assertTrue(page.source_schedule_time.isEnabled())
        self.assertTrue(page.source_schedule_interval.isEnabled())
        self.assertTrue(page.source_schedule_unit.isEnabled())
        self.assertFalse(page.start_time.isEnabled())
        page.source_schedule_time.setTime(QTime(7, 15))
        page.source_schedule_interval.setValue(2)
        page.source_schedule_unit.setCurrentIndex(2)
        self.assertEqual(page.settings()["source_schedule_time"], "07:15")
        self.assertEqual(page.settings()["source_schedule_interval"], 2)
        self.assertEqual(page.settings()["source_schedule_unit"], "weeks")
        self.assertEqual(page.settings()["schedule"]["start_time"], "22:00")
        page.schedule_enabled.setChecked(True)
        self.assertTrue(page.start_time.isEnabled())
        page.start_time.setTime(QTime(8, 30))
        self.assertEqual(page.settings()["source_schedule_time"], "07:15")
        self.assertTrue(page.interval.isEnabled())
        page.interval.setValue(3)
        page.interval_unit.setCurrentIndex(0)
        self.assertEqual(page.settings()["source_schedule_interval"], 2)
        self.assertEqual(page.settings()["source_schedule_unit"], "weeks")
        self.assertEqual(page.settings()["schedule"]["unit"], "hours")
        self.assertEqual(page.settings()["schedule"]["interval"], 3)
        page.source_schedule_enabled.setChecked(False)
        self.assertFalse(page.source_schedule_interval.isEnabled())
        self.assertTrue(page.interval.isEnabled())
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

    def test_configuration_round_trip_restores_each_tab(self):
        first = self.window.pages[SITES[0]]
        second = self.window.pages[SITES[1]]
        self.window.proxy_enabled.setChecked(True)
        self.window.proxy_server.setText("http://ru-proxy.example:8080")
        self.window.proxy_username.setText("proxy-user")
        self.window.proxy_password.setText("proxy-secret")
        first.source_url.setText("https://first.example/products.json")
        first.token.setText("secret-token")
        first.source_schedule_enabled.setChecked(True)
        first.source_schedule_time.setTime(QTime(7, 15))
        first.source_schedule_interval.setValue(2)
        first.source_schedule_unit.setCurrentIndex(0)
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
        expected_proxy = self.window.proxy_settings()

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "saved.json"
            self.window.save_config_to(path)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-token", raw)
            self.assertNotIn("secret-password", raw)
            self.assertNotIn("proxy-secret", raw)
            self.window.proxy_enabled.setChecked(False)
            self.window.proxy_server.clear()
            self.window.proxy_username.clear()
            self.window.proxy_password.clear()
            for page in self.window.pages.values():
                page.source_url.clear()
                page.token.clear()
                page.ftp_password.clear()
                page.source_schedule_enabled.setChecked(False)
                page.source_schedule_time.setTime(QTime(22, 0))
                page.source_schedule_interval.setValue(1)
                page.source_schedule_unit.setCurrentIndex(1)
            self.window.load_config_from(path)

        self.assertEqual(
            {site: page.settings() for site, page in self.window.pages.items()}, expected
        )
        self.assertEqual(self.window.proxy_settings(), expected_proxy)

    def test_proxy_fields_are_shared_and_disabled_until_enabled(self):
        self.assertFalse(self.window.proxy_enabled.isChecked())
        self.assertFalse(self.window.proxy_server.isEnabled())
        self.window.proxy_enabled.setChecked(True)
        self.assertTrue(self.window.proxy_server.isEnabled())
        self.assertEqual(self.window.proxy_password.echoMode(), QLineEdit.EchoMode.Password)

    def test_invalid_proxy_prevents_browser_walk(self):
        page = self.window.pages[SITES[0]]
        chrome = BrowserInstallation("Google Chrome", Path("C:/Browsers/Chrome/chrome.exe"))
        page.set_available_browsers({chrome.name: chrome})
        page.browser.setCurrentIndex(page.browser.findData(chrome.name))
        self._load_links(page, [ProductLinks(
            "42", "https://ozon.example/42", "https://wildberries.example/42",
            "https://market.example/42",
        )])
        self.window.proxy_enabled.setChecked(True)
        self.window.proxy_server.setText("invalid-proxy")
        page.start_button.click()
        self.assertFalse(page.walk_pending)
        self.assertEqual(page.state_label.text(), "Ошибка прокси")
        self.assertIn("Прокси", page.run_status.text())

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
        with patch("web_parser.ui.main_window.discover_browsers", return_value={chrome.name: chrome}) as discover:
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
