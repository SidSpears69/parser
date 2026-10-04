"""Qt6 user interface for Web Parser."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTime, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTimeEdit,
    QVBoxLayout, QWidget,
)

from web_parser import config


SITES = config.SITES
BROWSERS = config.BROWSERS


def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


def field(placeholder: str, name: str, secret: bool = False) -> QLineEdit:
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    widget.setObjectName(name)
    widget.setClearButtonEnabled(True)
    if secret:
        widget.setEchoMode(QLineEdit.EchoMode.Password)
    return widget


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("section")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 13, 16, 15)
    layout.setSpacing(10)
    layout.addWidget(label(title, "sectionTitle"))
    return frame, layout


def form() -> QFormLayout:
    layout = QFormLayout()
    layout.setSpacing(9)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return layout


class ErrorLog(QWidget):
    """Bounded, per-site error log. Safe to append through a queued Qt signal."""

    error_received = Signal(str, str, str)

    def __init__(self, site: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.site = site
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        toolbar = QHBoxLayout()
        toolbar.addWidget(label("Журнал ошибок", "sectionTitle"))
        self.count_label = label("0 записей", "hint")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch()
        self.save_button = QPushButton("Сохранить TXT…")
        self.clear_button = QPushButton("Очистить")
        toolbar.addWidget(self.save_button)
        toolbar.addWidget(self.clear_button)
        layout.addLayout(toolbar)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("errorLog")
        self.editor.setReadOnly(True)
        self.editor.setMaximumBlockCount(2000)
        self.editor.setPlaceholderText(
            "Ошибок пока нет.\nЗдесь появятся время события, ID товара, URL и описание ошибки."
        )
        self.editor.setMinimumHeight(130)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.editor)
        self.save_button.clicked.connect(self._choose_export)
        self.clear_button.clicked.connect(self.editor.clear)
        self.editor.textChanged.connect(self._refresh)
        self.error_received.connect(self.append_error)
        self._refresh()

    @Slot(str, str, str)
    def append_error(self, product_id: str, url: str, message: str) -> None:
        # One event per line keeps the bounded log and its counter consistent.
        clean = lambda value: " ".join(str(value).split())
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        self.editor.appendPlainText(
            f"[{timestamp}] ID: {clean(product_id) or '—'} | "
            f"URL: {clean(url) or '—'} | {clean(message)}"
        )

    def export_to(self, path: str | Path) -> None:
        Path(path).write_text(self.editor.toPlainText() + "\n", encoding="utf-8")

    def _choose_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить журнал ошибок", f"{self.site}-errors.txt", "Текстовые файлы (*.txt)"
        )
        if path:
            try:
                self.export_to(path)
            except OSError as error:
                QMessageBox.warning(self, "Не удалось сохранить журнал", str(error))

    def _refresh(self) -> None:
        has_text = not self.editor.document().isEmpty()
        count = self.editor.document().blockCount() if has_text else 0
        self.count_label.setText(f"Записей: {count} / 2000")
        self.save_button.setEnabled(has_text)
        self.clear_button.setEnabled(has_text)


class SitePage(QWidget):
    start_requested = Signal(str)
    stop_requested = Signal(str)

    def __init__(self, site: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.site = site
        self.setObjectName("sitePage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(14)

        toolbar = QHBoxLayout()
        toolbar.addWidget(label("Сбор данных", "sectionTitle"))
        self.state_label = label("Не запущен", "state")
        toolbar.addWidget(self.state_label)
        toolbar.addStretch()
        self.start_button = QPushButton("Запустить")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.stop_button)
        layout.addLayout(toolbar)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        source, source_layout = section("Источник данных")
        source_form = form()
        self.source_url = field("https://example.ru/export/products.json", "sourceUrl")
        self.token = field("Токен доступа к JSON", "token", secret=True)
        source_form.addRow("URL списка товаров", self.source_url)
        source_form.addRow("Токен доступа", self.token)
        source_layout.addLayout(source_form)
        source_hint = label("JSON со списком ID товаров и ссылками на маркетплейсы.", "hint")
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)
        source_layout.addStretch()
        grid.addWidget(source, 0, 0)

        ftp, ftp_layout = section("Выгрузка на FTP")
        ftp_form = form()
        self.ftp_host = field("ftp.example.ru", "ftpHost")
        self.ftp_port = QSpinBox()
        self.ftp_port.setObjectName("ftpPort")
        self.ftp_port.setRange(1, 65535)
        self.ftp_port.setValue(21)
        self.ftp_port.setFixedWidth(92)
        connection = QHBoxLayout()
        connection.addWidget(self.ftp_host, 1)
        port_label = label("Порт")
        port_label.setBuddy(self.ftp_port)
        connection.addWidget(port_label)
        connection.addWidget(self.ftp_port)
        self.ftp_user = field("Логин FTP", "ftpUser")
        self.ftp_password = field("Пароль FTP", "ftpPassword", secret=True)
        credentials = QHBoxLayout()
        credentials.addWidget(self.ftp_user)
        credentials.addWidget(self.ftp_password)
        self.ftp_user.setAccessibleName("Логин FTP")
        self.ftp_password.setAccessibleName("Пароль FTP")
        self.ftp_host.setAccessibleName("Адрес FTP")
        self.remote_path = field("/site/parser/", "remotePath")
        ftp_form.addRow("Сервер", connection)
        ftp_form.addRow("Логин / пароль", credentials)
        ftp_form.addRow("Каталог JSON", self.remote_path)
        ftp_layout.addLayout(ftp_form)
        grid.addWidget(ftp, 0, 1)

        browser, browser_layout = section("Браузер и обработка")
        browser_form = form()
        self.browser = QComboBox()
        self.browser.setObjectName("browser")
        self.browser.addItems(BROWSERS)
        self.browser.setToolTip("Список вариантов из ТЗ.")
        self.pause = QSpinBox()
        self.pause.setObjectName("pauseSeconds")
        self.pause.setRange(1, 3600)
        self.pause.setValue(5)
        self.pause.setSuffix(" сек.")
        browser_form.addRow("Браузер", self.browser)
        browser_form.addRow("Пауза между URL", self.pause)
        browser_layout.addLayout(browser_form)
        hint = label("Браузер используется для Ozon и Яндекс Маркета. Wildberries — через JSON.", "hint")
        hint.setWordWrap(True)
        browser_layout.addWidget(hint)
        browser_layout.addStretch()
        grid.addWidget(browser, 1, 0)

        schedule, schedule_layout = section("Расписание")
        self.schedule_enabled = QCheckBox("Запускать автоматически")
        self.schedule_enabled.setObjectName("scheduleEnabled")
        schedule_layout.addWidget(self.schedule_enabled)
        schedule_form = form()
        self.start_time = QTimeEdit(QTime(22, 0))
        self.start_time.setObjectName("startTime")
        self.start_time.setDisplayFormat("HH:mm")
        self.interval = QSpinBox()
        self.interval.setObjectName("interval")
        self.interval.setRange(1, 999)
        self.interval.setValue(1)
        self.interval_unit = QComboBox()
        self.interval_unit.setObjectName("intervalUnit")
        for title, value in (("часов", "hours"), ("дней", "days"), ("недель", "weeks"), ("месяцев", "months")):
            self.interval_unit.addItem(title, value)
        self.interval_unit.setCurrentIndex(1)
        interval_row = QHBoxLayout()
        interval_row.addWidget(self.interval)
        interval_row.addWidget(self.interval_unit)
        self.interval.setAccessibleName("Интервал повторения")
        self.interval_unit.setAccessibleName("Единица интервала")
        schedule_form.addRow("Время начала", self.start_time)
        schedule_form.addRow("Повторять каждые", interval_row)
        schedule_layout.addLayout(schedule_form)
        self.schedule_enabled.toggled.connect(self._toggle_schedule)
        self._toggle_schedule(False)
        grid.addWidget(schedule, 1, 1)
        self._settings_grid = grid
        self._panels = (source, browser, ftp, schedule)
        self._compact: bool | None = None
        layout.addLayout(grid)

        self.log = ErrorLog(site)
        layout.addWidget(self.log, 1)
        self.start_button.clicked.connect(lambda: self.start_requested.emit(self.site))
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self.site))

    def resizeEvent(self, event) -> None:
        compact = self.width() < 920
        if compact != self._compact:
            self._compact = compact
            for panel in self._panels:
                self._settings_grid.removeWidget(panel)
            for index, panel in enumerate(self._panels):
                row, column = (index, 0) if compact else (index % 2, index // 2)
                self._settings_grid.addWidget(panel, row, column)
            self._settings_grid.setColumnStretch(1, 0 if compact else 1)
        super().resizeEvent(event)

    def _toggle_schedule(self, enabled: bool) -> None:
        for widget in (self.start_time, self.interval, self.interval_unit):
            widget.setEnabled(enabled)

    def settings(self) -> dict:
        """Return the settings currently shown on this site's tab."""
        return {
            "site": self.site,
            "source_url": self.source_url.text().strip(),
            "token": self.token.text(),
            "browser": self.browser.currentText(),
            "pause_seconds": self.pause.value(),
            "ftp": {
                "host": self.ftp_host.text().strip(), "port": self.ftp_port.value(),
                "username": self.ftp_user.text(), "password": self.ftp_password.text(),
                "remote_path": self.remote_path.text().strip(),
            },
            "schedule": {
                "enabled": self.schedule_enabled.isChecked(),
                "start_time": self.start_time.time().toString("HH:mm"),
                "interval": self.interval.value(), "unit": self.interval_unit.currentData(),
            },
        }

    def apply_settings(self, settings: dict) -> None:
        """Restore a validated site's settings to its controls."""
        self.source_url.setText(settings["source_url"])
        self.token.setText(settings["token"])
        self.browser.setCurrentText(settings["browser"])
        self.pause.setValue(settings["pause_seconds"])
        ftp = settings["ftp"]
        self.ftp_host.setText(ftp["host"])
        self.ftp_port.setValue(ftp["port"])
        self.ftp_user.setText(ftp["username"])
        self.ftp_password.setText(ftp["password"])
        self.remote_path.setText(ftp["remote_path"])
        schedule = settings["schedule"]
        self.schedule_enabled.setChecked(schedule["enabled"])
        self.start_time.setTime(QTime.fromString(schedule["start_time"], "HH:mm"))
        self.interval.setValue(schedule["interval"])
        self.interval_unit.setCurrentIndex(self.interval_unit.findData(schedule["unit"]))


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
        self.save_config_button = QPushButton("Сохранить…")
        self.load_config_button.setToolTip("Загрузить настройки всех вкладок из файла")
        self.save_config_button.setToolTip("Сохранить настройки всех вкладок в файл")
        config_layout.addWidget(self.load_config_button)
        config_layout.addWidget(self.save_config_button)
        layout.addLayout(config_layout)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("siteTabs")
        self.tabs.setDocumentMode(True)
        self.pages: dict[str, SitePage] = {}
        for site in SITES:
            page = SitePage(site)
            self.pages[site] = page
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, site)
            page.start_requested.connect(self._collection_unavailable)
        layout.addWidget(self.tabs, 1)
        self.save_config_button.clicked.connect(self._choose_save_config)
        self.load_config_button.clicked.connect(self._choose_load_config)

    def save_config_to(self, path: str | Path) -> None:
        config.save(path, {site: page.settings() for site, page in self.pages.items()})
        self.statusBar().showMessage(f"Настройки сохранены в {Path(path).name}")

    def load_config_from(self, path: str | Path) -> None:
        settings_by_site = config.load(path)
        if set(settings_by_site) != set(SITES):
            raise ValueError("Файл должен содержать настройки всех четырёх сайтов")
        for site in SITES:
            self.pages[site].apply_settings(settings_by_site[site])
        self.statusBar().showMessage(f"Настройки загружены из {Path(path).name}")

    def _choose_save_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить настройки", "web-parser-config.json", "Файлы JSON (*.json)"
        )
        if not path:
            return
        try:
            self.save_config_to(path)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось сохранить настройки", str(error))

    def _choose_load_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить настройки", "", "Файлы JSON (*.json)"
        )
        if not path:
            return
        try:
            self.load_config_from(path)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "Не удалось загрузить настройки", str(error))

    @Slot(str)
    def _collection_unavailable(self, site: str) -> None:
        self.statusBar().showMessage(f"{site}: модуль сбора ещё не подключён. Запуск не выполнен.")
