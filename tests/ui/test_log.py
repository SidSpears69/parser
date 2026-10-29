"""Per-site error log behavior."""

from pathlib import Path
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from web_parser.ui import SITES

from .base import UiTestCase


class LogTests(UiTestCase):
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

    def test_log_limit_and_start_requires_loaded_links(self):
        page = self.window.pages[SITES[0]]
        for index in range(2005):
            page.log.append_error(str(index), "", "Ошибка")
        self.assertEqual(page.log.editor.document().blockCount(), 2000)
        self.assertNotIn("ID: 0 |", page.log.editor.toPlainText())
        QTest.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
        self.assertEqual(page.state_label.text(), "Не запущен")
        self.assertFalse(page.start_button.isEnabled())
        self.assertFalse(page.stop_button.isEnabled())
        self.assertIn("Загрузите ссылки", page.run_status.text())
        self.assertTrue(self.window.save_config_button.isEnabled())
