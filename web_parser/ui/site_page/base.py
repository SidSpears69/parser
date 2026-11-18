"""Widgets and shared state for a source-site tab."""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from web_parser import config
from web_parser.browser_discovery import BrowserInstallation
from web_parser.images import SavedImage
from web_parser.ozon import OzonProduct
from web_parser.product_links import ProductLinks
from web_parser.ui.links import LinksPreviewTable
from web_parser.ui.log import ErrorLog
from web_parser.ui.ozon import OzonResultsTable
from web_parser.ui.results import MarketplaceResultsTable
from web_parser.ui.scheduler import TabScheduler
from web_parser.ui.widgets import (
    ReadableComboBox, ReadableSpinBox, ReadableTimeEdit, field, form, label, section,
)
from web_parser.ui.workers import BrowserWalkThread, ProductLinksFetchThread
from web_parser.ui.wildberries import WildberriesResultsTable
from web_parser.ui.yandex_market import YandexMarketResultsTable
from web_parser.wildberries import WildberriesProduct
from web_parser.yandex_market import YandexMarketProduct

BROWSERS = config.BROWSERS
_INTERVAL_LABELS = ("часов", "дней", "недель", "месяцев")


def _soft_wrap(text: str, chunk_size: int = 32) -> str:
    """Allow long URL and path fragments to wrap without visible separators."""
    return "\u200b".join(text[index:index + chunk_size] for index in range(0, len(text), chunk_size))


def _results_panel(
        title: str, table: MarketplaceResultsTable, object_name: str,
) -> tuple[QWidget, QLabel]:
    panel, panel_layout = section(title)
    table.setObjectName(object_name)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.setWordWrap(True)
    table.setMinimumHeight(190)
    panel_layout.addWidget(table)
    hint = label("После запуска здесь появятся первые шесть товаров каждой выдачи.", "hint")
    hint.setWordWrap(True)
    panel_layout.addWidget(hint)
    return panel, hint


class SitePageBase(QWidget):
    refresh_browsers_requested = Signal()
    walk_finished = Signal()
    walk_state_changed = Signal()
    links_fetch_finished = Signal()
    fatal_layout = Signal(str, str, str, str, str)

    def __init__(
            self, site: str, parent: QWidget | None = None,
            *, proxy_provider: Callable[[], dict[str, str] | None] = lambda: None,
            proxy_secrets: Callable[[], tuple[str, str]] = lambda: ("", ""),
            extra_secrets: Callable[[], tuple[str, ...]] = lambda: (),
            log_root: Path | None = None,
    ):
        super().__init__(parent)
        self.site = site
        self._proxy_provider = proxy_provider
        self._available_browsers: dict[str, BrowserInstallation] = {}
        self._walk_thread: BrowserWalkThread | None = None
        self._links_thread: ProductLinksFetchThread | None = None
        self._product_links: tuple[ProductLinks, ...] = ()
        self._ozon_results: dict[str, tuple[OzonProduct, ...]] = {}
        self._wildberries_results: dict[str, tuple[WildberriesProduct, ...]] = {}
        self._yandex_results: dict[str, tuple[YandexMarketProduct, ...]] = {}
        self._saved_images: dict[tuple[str, str], tuple[SavedImage, ...]] = {}
        self._pending_scheduled_walk = False
        self._shutting_down = False
        self._emergency_stopped = False
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
        self.start_button.setEnabled(False)
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
        self.source_schedule_enabled = QCheckBox("По расписанию")
        self.source_schedule_enabled.setObjectName("sourceScheduleEnabled")
        source_layout.addWidget(self.source_schedule_enabled)
        source_schedule_form = form()
        self.source_schedule_time = ReadableTimeEdit(QTime(22, 0))
        self.source_schedule_time.setObjectName("sourceScheduleTime")
        self.source_schedule_time.setDisplayFormat("HH:mm")
        self.source_schedule_interval = ReadableSpinBox()
        self.source_schedule_interval.setObjectName("sourceScheduleInterval")
        self.source_schedule_interval.setRange(1, 999)
        self.source_schedule_interval.setValue(1)
        self.source_schedule_unit = ReadableComboBox()
        self.source_schedule_unit.setObjectName("sourceScheduleUnit")
        for title, value in zip(_INTERVAL_LABELS, config.INTERVAL_UNITS):
            self.source_schedule_unit.addItem(title, value)
        self.source_schedule_unit.setCurrentIndex(1)
        source_interval_row = QHBoxLayout()
        source_interval_row.addWidget(self.source_schedule_interval)
        source_interval_row.addWidget(self.source_schedule_unit)
        self.source_schedule_interval.setAccessibleName("Интервал загрузки JSON")
        self.source_schedule_unit.setAccessibleName("Единица интервала загрузки JSON")
        source_schedule_form.addRow("Время загрузки JSON", self.source_schedule_time)
        source_schedule_form.addRow("Повторять каждые", source_interval_row)
        source_layout.addLayout(source_schedule_form)
        self.source_next_label = label("Следующая загрузка: выключена.", "hint")
        source_layout.addWidget(self.source_next_label)
        source_hint = label("JSON со списком ID товаров и ссылками на маркетплейсы.", "hint")
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)
        source_actions = QHBoxLayout()
        self.load_links_button = QPushButton("Загрузить ссылки")
        self.load_links_button.setObjectName("loadLinks")
        self.load_links_button.setEnabled(False)
        source_actions.addWidget(self.load_links_button)
        source_actions.addStretch()
        source_layout.addLayout(source_actions)
        self.links_status = label("Ссылки ещё не загружены.", "hint")
        self.links_status.setWordWrap(True)
        source_layout.addWidget(self.links_status)
        self.last_load_label = label("Последняя успешная загрузка: ещё не было.", "hint")
        source_layout.addWidget(self.last_load_label)
        source_layout.addStretch()
        grid.addWidget(source, 0, 0)

        ftp, ftp_layout = section("Выгрузка на FTP")
        ftp_form = form()
        self.ftp_host = field("ftp.example.ru", "ftpHost")
        self.ftp_port = ReadableSpinBox()
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
        self.remote_path = field("/", "remotePath")
        self.remote_path.setToolTip(
            "Для каталога входа на FTP укажите /. JSON сохраняется здесь, фото — в images/."
        )
        ftp_form.addRow("Сервер", connection)
        ftp_form.addRow("Логин / пароль", credentials)
        ftp_form.addRow("Базовый каталог", self.remote_path)
        ftp_layout.addLayout(ftp_form)
        self.ftp_status = label("При настроенном FTP выгрузка начнётся после сбора данных.", "hint")
        self.ftp_status.setTextFormat(Qt.TextFormat.PlainText)
        self.ftp_status.setWordWrap(True)
        self.ftp_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ftp_layout.addWidget(self.ftp_status)
        grid.addWidget(ftp, 0, 1)

        browser, browser_layout = section("Браузер и обработка")
        browser_form = form()
        self.browser = ReadableComboBox()
        self.browser.setObjectName("browser")
        for browser_name in BROWSERS:
            self.browser.addItem(browser_name, browser_name)
        self.browser.setToolTip("Выберите браузер для обхода загруженных ссылок.")
        self.refresh_browsers_button = QPushButton("Обновить")
        self.refresh_browsers_button.setObjectName("refreshBrowsers")
        self.refresh_browsers_button.setToolTip("Повторно найти браузеры на этом компьютере")
        browser_row = QHBoxLayout()
        browser_row.addWidget(self.browser, 1)
        browser_row.addWidget(self.refresh_browsers_button)
        self.pause = ReadableSpinBox()
        self.pause.setObjectName("pauseSeconds")
        self.pause.setRange(1, 3600)
        self.pause.setValue(5)
        self.pause.setSuffix(" сек.")
        browser_form.addRow("Браузер", browser_row)
        browser_form.addRow("Пауза между URL", self.pause)
        browser_layout.addLayout(browser_form)
        self.browser_path_label = label("Установленный браузер не найден.", "hint")
        self.browser_path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.browser_path_label.setWordWrap(True)
        self.browser_path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        browser_layout.addWidget(self.browser_path_label)
        self.run_status = label("Загрузите ссылки из JSON, затем нажмите «Запустить».", "hint")
        self.run_status.setTextFormat(Qt.TextFormat.PlainText)
        self.run_status.setWordWrap(True)
        self.run_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        browser_layout.addWidget(self.run_status)
        browser_layout.addStretch()
        grid.addWidget(browser, 1, 0)

        schedule, schedule_layout = section("Расписание")
        self.schedule_enabled = QCheckBox("Запускать автоматически")
        self.schedule_enabled.setObjectName("scheduleEnabled")
        schedule_layout.addWidget(self.schedule_enabled)
        schedule_form = form()
        self.start_time = ReadableTimeEdit(QTime(22, 0))
        self.start_time.setObjectName("startTime")
        self.start_time.setDisplayFormat("HH:mm")
        self.interval = ReadableSpinBox()
        self.interval.setObjectName("interval")
        self.interval.setRange(1, 999)
        self.interval.setValue(1)
        self.interval_unit = ReadableComboBox()
        self.interval_unit.setObjectName("intervalUnit")
        for title, value in zip(_INTERVAL_LABELS, config.INTERVAL_UNITS):
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
        self.analysis_next_label = label("Следующий анализ: выключен.", "hint")
        schedule_layout.addWidget(self.analysis_next_label)
        grid.addWidget(schedule, 1, 1)
        self._settings_grid = grid
        self._panels = (source, browser, ftp, schedule)
        self._compact: bool | None = None
        layout.addLayout(grid)

        links_panel, links_layout = section("Ссылки товаров")
        self.links_preview = LinksPreviewTable()
        self.links_preview.setObjectName("linksPreview")
        self.links_preview.setHorizontalHeaderLabels(
            ("ID", "Ozon", "Wildberries", "Яндекс Маркет")
        )
        self.links_preview.verticalHeader().setVisible(False)
        self.links_preview.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.links_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.links_preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.links_preview.setWordWrap(True)
        self.links_preview.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.links_preview.setAlternatingRowColors(True)
        self.links_preview.setMinimumHeight(160)
        links_layout.addWidget(self.links_preview)
        self.links_preview_hint = label("Загрузите JSON, чтобы увидеть ID и ссылки товаров.", "hint")
        links_layout.addWidget(self.links_preview_hint)
        layout.addWidget(links_panel)

        self.ozon_table = OzonResultsTable()
        ozon_panel, self.ozon_hint = _results_panel(
            "Результаты Ozon", self.ozon_table, "ozonResults",
        )
        layout.addWidget(ozon_panel)

        self.wildberries_table = WildberriesResultsTable()
        wildberries_panel, self.wildberries_hint = _results_panel(
            "Результаты Wildberries", self.wildberries_table, "wildberriesResults",
        )
        layout.addWidget(wildberries_panel)

        self.yandex_table = YandexMarketResultsTable()
        yandex_panel, self.yandex_hint = _results_panel(
            "Результаты Яндекс Маркета", self.yandex_table, "yandexResults",
        )
        layout.addWidget(yandex_panel)

        self.log = ErrorLog(
            site, log_root=log_root,
            secret_provider=lambda: (
                self.token.text(), self.ftp_user.text(), self.ftp_password.text(),
                *proxy_secrets(),
                *extra_secrets(),
            ),
        )
        layout.addWidget(self.log, 1)
        self.scheduler = TabScheduler(self)

    def _update_browser_controls(self) -> None:
        raise NotImplementedError

    def _start_walk(self) -> None:
        raise NotImplementedError

    def settings(self) -> dict:
        raise NotImplementedError
