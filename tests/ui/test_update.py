"""The update button connects release checks to the installer handoff."""

import time
from threading import Event
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from tests.ui.base import UiTestCase
from web_parser.ui.update import UpdateProgressDialog
from web_parser.updates import Release, UpdateError


class UpdateUiTests(UiTestCase):
    def _confirm_dialog(self):
        dialogs = self.window.findChildren(QMessageBox)
        self.assertEqual(len(dialogs), 1)
        dialog = dialogs[0]
        buttons = {button.text(): button for button in dialog.buttons()}
        self.assertEqual(set(buttons), {"Да", "Нет"})
        buttons["Да"].click()

    @staticmethod
    def _release():
        return Release(
            "1.2.0", "v1.2.0", "WebParserSetup-1.2.0.exe",
            "https://github.com/SidSpears69/parser/releases/download/v1.2.0/WebParserSetup-1.2.0.exe",
            10, "0" * 64,
        )

    def test_update_button_downloads_and_starts_newer_release(self):
        release = self._release()
        with patch("web_parser.ui.main_window.installed_version", return_value="1.1.0"), \
                patch("web_parser.ui.update.latest_release", return_value=release), \
                patch("web_parser.ui.update.download_installer", return_value="C:/Temp/setup.exe"), \
                patch("web_parser.ui.main_window.QMessageBox.exec", side_effect=self._confirm_dialog), \
                patch.object(self.window, "_launch_installer") as launch:
            self.window.update_button.click()
            deadline = time.monotonic() + 4
            while not launch.called and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertTrue(launch.called)
        launch.assert_called_once_with("C:/Temp/setup.exe")

    def test_download_shows_progress_and_can_be_cancelled(self):
        started = Event()
        proceed = Event()

        def delayed_download(_release, progress, cancel_requested):
            progress(5, 10)
            started.set()
            proceed.wait(3)
            if cancel_requested():
                raise UpdateError("Загрузка отменена.")
            return "C:/Temp/setup.exe"

        with (
            patch("web_parser.ui.main_window.installed_version", return_value="1.1.0"),
            patch("web_parser.ui.update.latest_release", return_value=self._release()),
            patch("web_parser.ui.update.download_installer", side_effect=delayed_download),
            patch("web_parser.ui.main_window.QMessageBox.exec", side_effect=self._confirm_dialog),
            patch.object(self.window, "_launch_installer") as launch,
        ):
            self.window.show()
            try:
                self.window.update_button.click()
                deadline = time.monotonic() + 4
                while not started.is_set() and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertTrue(started.is_set())
                dialogs = self.window.findChildren(UpdateProgressDialog)
                self.assertEqual(len(dialogs), 1)
                dialog = dialogs[0]
                while dialog.progress_bar.value() != 50 and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertTrue(dialog.isVisible())
                self.assertTrue(self.window.isEnabled())
                self.assertEqual(dialog.progress_bar.value(), 50)
                self.assertIn("Скачано", dialog.status_label.text())
                dialog.cancel_button.click()
                self.assertIn("Отмена", dialog.status_label.text())
            finally:
                proceed.set()
            while self.window.statusBar().currentMessage() != "Загрузка обновления отменена" \
                    and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(self.window.statusBar().currentMessage(), "Загрузка обновления отменена")
            QTest.qWait(10)
            self.assertFalse(self.window.findChildren(UpdateProgressDialog))
            launch.assert_not_called()
