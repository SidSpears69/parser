"""Qt platform selection used by the desktop application."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from web_parser.app import create_application


class ApplicationArgumentsTests(unittest.TestCase):
    @staticmethod
    def _application_arguments(
        arguments: list[str], platform: str, qt_platform: str | None,
    ) -> list[str]:
        environment = {"QT_QPA_PLATFORM": qt_platform} if qt_platform is not None else {}
        with (
            patch("web_parser.app.QApplication") as application,
            patch("web_parser.app.sys", SimpleNamespace(platform=platform)),
            patch("web_parser.app.os", SimpleNamespace(environ=environment)),
        ):
            create_application(arguments)
            return application.call_args.args[0]

    def test_windows_uses_gdi_font_engine(self):
        self.assertEqual(
            self._application_arguments(["main.py"], "win32", None),
            ["main.py", "-platform", "windows:nodirectwrite"],
        )
        self.assertEqual(
            self._application_arguments(["main.py"], "win32", "windows:darkmode=1"),
            ["main.py", "-platform", "windows:darkmode=1,nodirectwrite"],
        )

    def test_explicit_platform_or_font_engine_is_preserved(self):
        for arguments, platform, qt_platform in (
            (["main.py"], "win32", "offscreen"),
            (["main.py"], "linux", None),
            (["main.py", "-platform", "windows"], "win32", None),
            (["main.py"], "win32", "windows:fontengine=freetype"),
            (["main.py"], "win32", "windows:nodirectwrite"),
        ):
            with self.subTest(arguments=arguments, platform=platform, qt_platform=qt_platform):
                self.assertEqual(
                    self._application_arguments(arguments, platform, qt_platform), arguments,
                )
