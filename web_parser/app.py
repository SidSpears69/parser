import os
import sys

from PySide6.QtCore import QLocale
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from web_parser.ui import MainWindow
from web_parser.theme import STYLESHEET


def _qt_arguments(argv: list[str], platform: str, qpa_platform: str | None) -> list[str]:
    """Use Windows' GDI font engine to handle legacy bitmap fonts."""
    if platform != "win32" or "-platform" in argv or any(
        argument.startswith("-platform=") for argument in argv
    ):
        return argv
    selected = qpa_platform or "windows"
    name, _, options = selected.partition(":")
    if name.lower() != "windows":
        return argv
    option_list = options.split(",") if options else []
    if "nodirectwrite" in option_list or any(
        option.startswith("fontengine=") for option in option_list
    ):
        return argv
    selected += ",nodirectwrite" if options else ":nodirectwrite"
    return [*argv, "-platform", selected]


def create_application(argv: list[str] | None = None) -> QApplication:
    arguments = list(sys.argv if argv is None else argv) or ["web-parser"]
    app = QApplication(_qt_arguments(arguments, sys.platform, os.environ.get("QT_QPA_PLATFORM")))
    app.setApplicationName("Web Parser")
    app.setOrganizationName("Web Parser")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    app.setStyleSheet(STYLESHEET)
    return app


def main() -> int:
    app = create_application()
    window = MainWindow()
    window.show()
    return app.exec()
