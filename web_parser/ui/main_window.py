"""Main window and cross-site configuration controls."""

from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from web_parser import config
from web_parser.browser_discovery import discover_browsers
from web_parser.ui.site import SitePage
from web_parser.ui.widgets import ClickableCursorFilter, field, form, label, section

SITES = config.SITES

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web Parser — управление сбором данных")
        self.resize(1140, 900)
        self.setMinimumSize(850, 620)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(3)
        title.addWidget(label("Web Parser", "appTitle"))
        title.addWidget(label("Сбор данных с маркетплейсов", "subtitle"))
        header.addLayout(title)
        header.addStretch()
        header.addWidget(label("4 сайта", "badge"))
        layout.addLayout(header)

        config_layout = QHBoxLayout()
        config_layout.addWidget(label("Конфигурация всех сайтов", "sectionTitle"))
        config_layout.addStretch()
        self.load_config_button = QPushButton("Загрузить…")
        self.load_config_button.setObjectName("loadConfig")
        self.save_config_button = QPushButton("Сохранить…")
        self.save_config_button.setObjectName("saveConfig")
        self.load_config_button.setToolTip("Загрузить настройки всех вкладок из файла")
        self.save_config_button.setToolTip("Сохранить настройки всех вкладок в файл")
        config_layout.addWidget(self.load_config_button)
        config_layout.addWidget(self.save_config_button)
        layout.addLayout(config_layout)

        self.proxy_section, proxy_layout = section("Прокси для браузера")
        self.proxy_enabled = QCheckBox("Использовать прокси при обходе маркетплейсов")
        self.proxy_enabled.setObjectName("proxyEnabled")
        proxy_layout.addWidget(self.proxy_enabled)
        proxy_form = form()
        self.proxy_server = field("Протокол, адрес и порт прокси", "proxyServer")
        self.proxy_username = field("Логин (если требуется)", "proxyUsername")
        self.proxy_password = field("Пароль (если требуется)", "proxyPassword", secret=True)
        credentials = QHBoxLayout()
        credentials.addWidget(self.proxy_username)
        credentials.addWidget(self.proxy_password)
        self.proxy_username.setAccessibleName("Логин прокси")
        self.proxy_password.setAccessibleName("Пароль прокси")
        proxy_form.addRow("Адрес", self.proxy_server)
        proxy_form.addRow("Логин / пароль", credentials)
        proxy_layout.addLayout(proxy_form)
        self.proxy_enabled.toggled.connect(self._toggle_proxy)
        self._toggle_proxy(False)
        layout.addWidget(self.proxy_section)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("siteTabs")
        self.tabs.setDocumentMode(True)
        self._browsers_scanned = False
        self._close_pending = False
        self.pages: dict[str, SitePage] = {}
        for site in SITES:
            page = SitePage(site, proxy_provider=self._browser_proxy)
            self.pages[site] = page
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, site)
            page.refresh_browsers_requested.connect(self._refresh_browsers)
            page.walk_finished.connect(self._resume_close)
            page.walk_state_changed.connect(self._update_config_controls)
            page.links_fetch_finished.connect(self._resume_close)
        layout.addWidget(self.tabs, 1)
        self.save_config_button.clicked.connect(self._choose_save_config)
        self.load_config_button.clicked.connect(self._choose_load_config)
        self._refresh_browsers()
        self._clickable_cursor_filter = ClickableCursorFilter(self)
        clickable_widgets = (
            *self.findChildren(QAbstractButton),
            *self.findChildren(QComboBox),
            self.tabs.tabBar(),
        )
        for widget in clickable_widgets:
            widget.installEventFilter(self._clickable_cursor_filter)
            self._clickable_cursor_filter.update_cursor(widget)

    @Slot()
    def _refresh_browsers(self) -> None:
        available = discover_browsers()
        for page in self.pages.values():
            page.set_available_browsers(available)
            if not self._browsers_scanned and page.settings()["browser"] not in available:
                first_installed = next(iter(available), "Mozilla Firefox")
                page.browser.setCurrentIndex(page.browser.findData(first_installed))
        self._browsers_scanned = True
        self.statusBar().showMessage(f"Найдено браузеров: {len(available)}")

    def closeEvent(self, event) -> None:
        busy = [
            page for page in self.pages.values()
            if page.walk_pending or page.links_fetch_pending
        ]
        if busy:
            event.ignore()
            if not self._close_pending:
                self._close_pending = True
                self.statusBar().showMessage("Завершаются фоновые операции перед закрытием приложения…")
                self.setEnabled(False)
                for page in self.pages.values():
                    page.prepare_walk_shutdown()
            return
        for page in self.pages.values():
            page.prepare_walk_shutdown()
            page.shutdown_links()
        super().closeEvent(event)

    @Slot()
    def _resume_close(self) -> None:
        if self._close_pending and all(
            not page.walk_pending and not page.links_fetch_pending
            for page in self.pages.values()
        ):
            self._close_pending = False
            QTimer.singleShot(0, self.close)

    def save_config_to(self, path: str | Path) -> None:
        config.save(
            path, {site: page.settings() for site, page in self.pages.items()},
            self.proxy_settings(),
        )
        self.statusBar().showMessage(f"Настройки сохранены в {Path(path).name}")

    def load_config_from(self, path: str | Path) -> None:
        if any(page.walk_pending for page in self.pages.values()):
            raise ValueError("Остановите обход страниц перед загрузкой настроек.")
        loaded = config.load_document(path)
        if set(loaded.sites) != set(SITES):
            raise ValueError("Файл должен содержать настройки всех четырёх сайтов")
        for site in SITES:
            self.pages[site].apply_settings(loaded.sites[site])
        self._apply_proxy_settings(loaded.proxy)
        self.statusBar().showMessage(f"Настройки загружены из {Path(path).name}")

    def proxy_settings(self) -> dict[str, str | bool]:
        return {
            "enabled": self.proxy_enabled.isChecked(),
            "server": self.proxy_server.text().strip(),
            "username": self.proxy_username.text(),
            "password": self.proxy_password.text(),
        }

    def _browser_proxy(self) -> dict[str, str] | None:
        return config.playwright_proxy(self.proxy_settings())

    def _apply_proxy_settings(self, settings: dict[str, str | bool]) -> None:
        self.proxy_enabled.setChecked(bool(settings["enabled"]))
        self.proxy_server.setText(str(settings["server"]))
        self.proxy_username.setText(str(settings["username"]))
        self.proxy_password.setText(str(settings["password"]))

    @Slot(bool)
    def _toggle_proxy(self, enabled: bool) -> None:
        for widget in (self.proxy_server, self.proxy_username, self.proxy_password):
            widget.setEnabled(enabled)

    def _choose_save_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить настройки", "web-parser-config.json", "Файлы JSON (*.json)"
        )
        if not path:
            return
        try:
            self.save_config_to(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Не удалось сохранить настройки", str(error))

    def _choose_load_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить настройки", "", "Файлы JSON (*.json)"
        )
        if not path:
            return
        try:
            self.load_config_from(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Не удалось загрузить настройки", str(error))

    @Slot()
    def _update_config_controls(self) -> None:
        editable = not any(page.walk_pending for page in self.pages.values()) and not self._close_pending
        self.load_config_button.setEnabled(editable)
        self.proxy_section.setEnabled(editable)
