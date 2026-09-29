"""Offline UI checks: python -m unittest discover -s tests -v."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from web_parser.app import create_application
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
        self.assertFalse(self.window.save_config_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
