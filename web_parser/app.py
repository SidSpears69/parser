import sys

from PySide6.QtCore import QLocale
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from web_parser.ui import MainWindow
from web_parser.theme import STYLESHEET


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication(sys.argv if argv is None else argv)
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
