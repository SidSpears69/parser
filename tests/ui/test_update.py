"""The update button connects release checks to the installer handoff."""

import time
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from tests.ui.base import UiTestCase
from web_parser.updates import Release


class UpdateUiTests(UiTestCase):
    def test_update_button_downloads_and_starts_newer_release(self):
        def confirm_dialog():
            dialogs = self.window.findChildren(QMessageBox)
            self.assertEqual(len(dialogs), 1)
            dialog = dialogs[0]
            buttons = {button.text(): button for button in dialog.buttons()}
            self.assertEqual(set(buttons), {"Да", "Нет"})
            buttons["Да"].click()

        release = Release(
            "1.2.0", "v1.2.0", "WebParserSetup-1.2.0.exe",
            "https://github.com/SidSpears69/parser/releases/download/v1.2.0/WebParserSetup-1.2.0.exe",
            10, "0" * 64,
        )
        with patch("web_parser.ui.main_window.installed_version", return_value="1.1.0"), \
                patch("web_parser.ui.update.latest_release", return_value=release), \
                patch("web_parser.ui.update.download_installer", return_value="C:/Temp/setup.exe"), \
                patch("web_parser.ui.main_window.QMessageBox.exec", side_effect=confirm_dialog), \
                patch.object(self.window, "_launch_installer") as launch:
            self.window.update_button.click()
            deadline = time.monotonic() + 4
            while not launch.called and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertTrue(launch.called)
        launch.assert_called_once_with("C:/Temp/setup.exe")
