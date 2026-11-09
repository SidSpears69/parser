"""Per-site error log behavior."""

from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from web_parser.ui import SITES
from web_parser.ui.log import ErrorLog

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
        self.assertEqual(first.file.path.read_text(encoding="utf-8"), text)
        self.assertFalse(second.file.path.exists())
        QTest.mouseClick(first.clear_button, Qt.MouseButton.LeftButton)
        self.assertTrue(first.editor.document().isEmpty())
        self.assertFalse(first.save_button.isEnabled())
        restored = ErrorLog(SITES[0], log_root=first.file.path.parent)
        self.assertIn("Страница не найдена 404", restored.editor.toPlainText())
        restored.deleteLater()

    def test_secrets_are_hidden_in_widget_and_file(self):
        page = self.window.pages[SITES[0]]
        page.token.setText("private token")
        page.ftp_password.setText("ftp-password")
        self.window.proxy_password.setText("proxy-password")
        page.log.append_error(
            "5", "https://example.org/?token=private%20token",
            "FTP ftp-password; proxy proxy-password; token private token",
        )
        content = page.log.file.path.read_text(encoding="utf-8")
        self.assertEqual(page.log.editor.toPlainText() + "\n", content)
        for secret in ("private token", "private%20token", "ftp-password", "proxy-password"):
            self.assertNotIn(secret, content)
        self.assertIn("[скрыто]", content)

    def test_file_failure_keeps_error_visible(self):
        log = self.window.pages[SITES[0]].log
        with patch.object(log.file, "append", side_effect=OSError("disk error")):
            log.append_error("7", "", "Ошибка сети")
        self.assertIn("Ошибка сети", log.editor.toPlainText())
        self.assertIn("Не удалось записать", log.file_label.text())

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
