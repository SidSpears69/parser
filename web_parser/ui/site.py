"""Controls and state for one source-site tab."""

from datetime import datetime, time
from pathlib import Path
from typing import Callable, TypeVar

from PySide6.QtCore import Qt, QTime, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QTableWidgetItem, QVBoxLayout, QWidget,
)

from web_parser import config
from web_parser.browser_discovery import BrowserInstallation
from web_parser.images import SavedImage
from web_parser.ozon import OzonProduct
from web_parser.product_links import ProductLinks
from web_parser.scheduler import Schedule
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
_Product = TypeVar("_Product", OzonProduct, WildberriesProduct, YandexMarketProduct)


def _soft_wrap(text: str, chunk_size: int = 32) -> str:
    """Allow long URL and path fragments to wrap without visible separators."""
    return "\u200b".join(text[index:index + chunk_size] for index in range(0, len(text), chunk_size))


def _results_panel(
    title: str, table: MarketplaceResultsTable, object_name: str,
    on_photo_click: Callable[[int, int], None],
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
    table.cellClicked.connect(on_photo_click)
    panel_layout.addWidget(table)
    hint = label("После запуска здесь появятся первые шесть товаров каждой выдачи.", "hint")
    hint.setWordWrap(True)
    panel_layout.addWidget(hint)
    return panel, hint


class SitePage(QWidget):
    refresh_browsers_requested = Signal()
    walk_finished = Signal()
    walk_state_changed = Signal()
    links_fetch_finished = Signal()

    def __init__(
        self, site: str, parent: QWidget | None = None,
        *, proxy_provider: Callable[[], dict[str, str] | None] = lambda: None,
        proxy_secrets: Callable[[], tuple[str, str]] = lambda: ("", ""),
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
        self.source_schedule_enabled.toggled.connect(self._toggle_source_schedule)
        self._toggle_source_schedule(False)
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
        self.schedule_enabled.toggled.connect(self._toggle_schedule)
        self._toggle_schedule(False)
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
        self.links_preview.cellClicked.connect(self._open_preview_link)
        links_layout.addWidget(self.links_preview)
        self.links_preview_hint = label("Загрузите JSON, чтобы увидеть ID и ссылки товаров.", "hint")
        links_layout.addWidget(self.links_preview_hint)
        layout.addWidget(links_panel)

        self.ozon_table = OzonResultsTable()
        ozon_panel, self.ozon_hint = _results_panel(
            "Результаты Ozon", self.ozon_table, "ozonResults", self._open_ozon_photo,
        )
        layout.addWidget(ozon_panel)

        self.wildberries_table = WildberriesResultsTable()
        wildberries_panel, self.wildberries_hint = _results_panel(
            "Результаты Wildberries", self.wildberries_table, "wildberriesResults",
            self._open_wildberries_photo,
        )
        layout.addWidget(wildberries_panel)

        self.yandex_table = YandexMarketResultsTable()
        yandex_panel, self.yandex_hint = _results_panel(
            "Результаты Яндекс Маркета", self.yandex_table, "yandexResults",
            self._open_yandex_photo,
        )
        layout.addWidget(yandex_panel)

        self.log = ErrorLog(
            site, log_root=log_root,
            secret_provider=lambda: (
                self.token.text(), self.ftp_user.text(), self.ftp_password.text(),
                *proxy_secrets(),
            ),
        )
        layout.addWidget(self.log, 1)
        self.start_button.clicked.connect(self._start_walk)
        self.stop_button.clicked.connect(self._stop_walk)
        self.browser.currentIndexChanged.connect(self._update_browser_controls)
        self.refresh_browsers_button.clicked.connect(lambda: self.refresh_browsers_requested.emit())
        self.source_url.textChanged.connect(self._clear_product_links)
        self.token.textChanged.connect(self._clear_product_links)
        self.load_links_button.clicked.connect(self._fetch_product_links)
        self.scheduler = TabScheduler(self)
        self.scheduler.source_due.connect(self._scheduled_source_due)
        self.scheduler.analysis_due.connect(self._scheduled_analysis_due)
        self.scheduler.changed.connect(self._update_schedule_status)
        self.source_schedule_enabled.toggled.connect(self._configure_source_schedule)
        self.source_schedule_time.timeChanged.connect(self._configure_source_schedule)
        self.source_schedule_interval.valueChanged.connect(self._configure_source_schedule)
        self.source_schedule_unit.currentIndexChanged.connect(self._configure_source_schedule)
        self.schedule_enabled.toggled.connect(self._configure_analysis_schedule)
        self.start_time.timeChanged.connect(self._configure_analysis_schedule)
        self.interval.valueChanged.connect(self._configure_analysis_schedule)
        self.interval_unit.currentIndexChanged.connect(self._configure_analysis_schedule)
        self._configure_source_schedule()
        self._configure_analysis_schedule()

    @property
    def product_links(self) -> tuple[ProductLinks, ...]:
        """Current parsed links for this site, in source order."""
        return self._product_links

    @property
    def wildberries_results(self) -> dict[str, tuple[WildberriesProduct, ...]]:
        return self._wildberries_results.copy()

    @property
    def ozon_results(self) -> dict[str, tuple[OzonProduct, ...]]:
        return self._ozon_results.copy()

    @property
    def yandex_results(self) -> dict[str, tuple[YandexMarketProduct, ...]]:
        return self._yandex_results.copy()

    @property
    def saved_images(self) -> dict[tuple[str, str], tuple[SavedImage, ...]]:
        return self._saved_images.copy()

    def _clear_ozon_results(self) -> None:
        self._ozon_results = {}
        self.ozon_table.reset_link_hover()
        self.ozon_table.setRowCount(0)
        self.ozon_hint.setText("После запуска здесь появятся первые шесть товаров каждой выдачи.")

    def _clear_wildberries_results(self) -> None:
        self._wildberries_results = {}
        self.wildberries_table.reset_link_hover()
        self.wildberries_table.setRowCount(0)
        self.wildberries_hint.setText("После запуска здесь появятся первые шесть товаров каждой выдачи.")

    def _clear_yandex_results(self) -> None:
        self._yandex_results = {}
        self.yandex_table.reset_link_hover()
        self.yandex_table.setRowCount(0)
        self.yandex_hint.setText("После запуска здесь появятся первые шесть товаров каждой выдачи.")

    @property
    def links_fetch_in_progress(self) -> bool:
        return self._links_thread is not None and self._links_thread.isRunning()

    @property
    def links_fetch_pending(self) -> bool:
        return self._links_thread is not None

    def _clear_product_links(self) -> None:
        self._pending_scheduled_walk = False
        if self._links_thread is None:
            self._product_links = ()
            self._clear_ozon_results()
            self._clear_wildberries_results()
            self._clear_yandex_results()
            self._saved_images = {}
            self.links_preview.reset_link_hover()
            self.links_preview.setRowCount(0)
            self.links_status.setText("Ссылки ещё не загружены.")
            self.links_preview_hint.setText("Загрузите JSON, чтобы увидеть ID и ссылки товаров.")
            if self._walk_thread is None:
                self.state_label.setText("Не запущен")
                self.run_status.setText("Загрузите ссылки из JSON, затем нажмите «Запустить».")
        self._update_links_controls()

    def _update_links_controls(self) -> None:
        fetching = self._links_thread is not None
        walking = self._walk_thread is not None
        self.source_url.setEnabled(not fetching and not walking)
        self.token.setEnabled(not fetching and not walking)
        self.load_links_button.setEnabled(
            bool(self.source_url.text().strip() and self.token.text())
            and not fetching and not walking and not self._shutting_down
        )
        self._update_browser_controls()

    def _fetch_product_links(self) -> None:
        if self._links_thread is not None or self._walk_thread is not None:
            return
        source_url = self.source_url.text().strip()
        token = self.token.text()
        if not source_url or not token:
            return
        self._product_links = ()
        self._clear_ozon_results()
        self._clear_wildberries_results()
        self._clear_yandex_results()
        self._saved_images = {}
        self.links_preview.reset_link_hover()
        self.links_preview.setRowCount(0)
        self._update_browser_controls()
        self.links_preview_hint.setText("Ожидание ответа источника…")
        self.links_status.setText("Загрузка JSON и разбор ссылок…")
        self.state_label.setText("Не запущен")
        self.run_status.setText("Загрузка ссылок из JSON…")
        thread = ProductLinksFetchThread(source_url, token, self)
        self._links_thread = thread
        thread.finished.connect(self._product_links_fetched)
        thread.start()
        self._update_links_controls()

    @Slot()
    def _product_links_fetched(self) -> None:
        thread = self._links_thread
        if thread is None:
            return
        self._links_thread = None
        if self._shutting_down:
            thread.token = ""
            thread.deleteLater()
            self.links_fetch_finished.emit()
            return
        source_changed = (
            self.source_url.text().strip() != thread.source_url
            or self.token.text() != thread.token
        )
        if source_changed:
            self.links_status.setText("Источник или токен изменились во время загрузки. Загрузите ссылки снова.")
            self.links_preview_hint.setText("Данные прежнего источника отброшены.")
            self.run_status.setText("Загрузите ссылки для текущего источника.")
        elif thread.error is not None or thread.links is None:
            message = thread.error or "Загрузка прервана из-за внутренней ошибки."
            self.links_status.setText(f"Не удалось загрузить ссылки: {message}")
            self.links_preview_hint.setText("Проверьте URL и токен доступа.")
            self.run_status.setText("Ссылки не загружены. Проверьте источник и токен.")
            self.log.append_error("", "", f"Сбор ссылок: {message}")
        else:
            self._product_links = tuple(thread.links)
            self._show_product_links()
            self.last_load_label.setText(
                f"Последняя успешная загрузка: {datetime.now():%d.%m.%Y %H:%M:%S}"
            )
        self._update_links_controls()
        thread.token = ""
        thread.deleteLater()
        if self._pending_scheduled_walk:
            self._pending_scheduled_walk = False
            if self._product_links:
                self._begin_walk(headless=True)
            else:
                self.log.append_error("", "", "Расписание анализа: ссылки не загружены; запуск пропущен.")
        self.links_fetch_finished.emit()

    def _show_product_links(self) -> None:
        total = len(self._product_links)
        shown = min(total, 20)
        self.links_preview.reset_link_hover()
        self.links_preview.setRowCount(shown)
        for row, links in enumerate(self._product_links[:shown]):
            values = (
                links.product_id, links.ozon_url, links.wildberries_url,
                links.yandex_market_url,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value or "—")
                if value:
                    item.setToolTip(value)
                if column > 0 and value:
                    item.setData(Qt.ItemDataRole.UserRole, value)
                    item.setForeground(LinksPreviewTable.LINK_COLOR)
                self.links_preview.setItem(row, column, item)
        self.links_status.setText(f"Загружено товаров: {total}")
        if total:
            self.run_status.setText(f"Готово к обходу: {total} товаров, {total * 3} страниц.")
        else:
            self.run_status.setText("В загруженном JSON нет ссылок для обхода.")
        self.links_preview_hint.setText(
            f"Показаны первые {shown} из {total} товаров." if total > shown
            else "Все загруженные товары показаны в таблице." if total
            else "В источнике нет товаров."
        )

    @Slot(int, int)
    def _open_preview_link(self, row: int, column: int) -> None:
        if column == 0:
            return
        item = self.links_preview.item(row, column)
        if item is not None:
            url = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(url, str):
                QDesktopServices.openUrl(QUrl(url))

    def _open_wildberries_photo(self, row: int, column: int) -> None:
        self._open_results_photo(self.wildberries_table, row, column)

    def _open_ozon_photo(self, row: int, column: int) -> None:
        self._open_results_photo(self.ozon_table, row, column)

    def _open_yandex_photo(self, row: int, column: int) -> None:
        self._open_results_photo(self.yandex_table, row, column)

    @staticmethod
    def _open_results_photo(table: MarketplaceResultsTable, row: int, column: int) -> None:
        if column != 6:
            return
        item = table.item(row, column)
        url = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(url, str):
            QDesktopServices.openUrl(QUrl(url))

    def shutdown_links(self) -> None:
        """Release a completed fetch worker when the window closes."""
        self._shutting_down = True
        self.scheduler.stop()
        if self._links_thread is not None:
            self._links_thread.wait()

    @property
    def walk_pending(self) -> bool:
        """Whether a traversal worker still belongs to this page."""
        return self._walk_thread is not None

    def prepare_walk_shutdown(self) -> None:
        self._shutting_down = True
        self.scheduler.stop()
        if self._walk_thread is not None:
            self._walk_thread.stop()

    def set_available_browsers(self, available: dict[str, BrowserInstallation]) -> None:
        """Refresh installation hints while retaining canonical config values."""
        self._available_browsers = available.copy()
        for index, browser_name in enumerate(BROWSERS):
            installation = available.get(browser_name)
            suffix = (
                "сборка Playwright" if browser_name == "Mozilla Firefox"
                else "найден" if installation else "не найден"
            )
            self.browser.setItemText(index, f"{browser_name} — {suffix}")
            self.browser.setItemData(
                index,
                "Используется сборка Playwright" if browser_name == "Mozilla Firefox"
                else str(installation.path) if installation else "",
                Qt.ItemDataRole.ToolTipRole,
            )
        self._update_browser_controls()

    def _selected_browser_name(self) -> str:
        return str(self.browser.currentData())

    def _selected_installation(self) -> BrowserInstallation | None:
        name = self._selected_browser_name()
        if name == "Mozilla Firefox":
            return self._available_browsers.get(name) or BrowserInstallation(name, Path())
        return self._available_browsers.get(name)

    def _update_browser_controls(self) -> None:
        name = self._selected_browser_name()
        installation = self._available_browsers.get(name)
        path_text = (
            "Firefox: используется сборка Playwright; наличие проверяется при запуске."
            if name == "Mozilla Firefox" else
            f"Путь: {_soft_wrap(str(installation.path))}"
            if installation else "Установленный браузер не найден."
        )
        self.browser_path_label.setText(path_text)
        self.browser_path_label.setToolTip(str(installation.path) if installation and name != "Mozilla Firefox" else "")
        walking = self._walk_thread is not None
        self.browser.setEnabled(not walking and not self._shutting_down)
        self.pause.setEnabled(not walking and not self._shutting_down)
        self.refresh_browsers_button.setEnabled(not walking and not self._shutting_down)
        for widget in (
            self.ftp_host, self.ftp_port, self.ftp_user, self.ftp_password, self.remote_path,
        ):
            widget.setEnabled(not walking and not self._shutting_down)
        self.start_button.setEnabled(
            bool(self._product_links) and self._selected_installation() is not None
            and not walking and self._links_thread is None and not self._shutting_down
        )
        self.stop_button.setEnabled(
            walking and not self._shutting_down
            and self._walk_thread is not None and not self._walk_thread.stop_requested
        )

    def _start_walk(self) -> None:
        self._begin_walk(headless=False)

    def _begin_walk(self, *, headless: bool) -> None:
        installation = self._selected_installation()
        if (
            installation is None or not self._product_links or self._walk_thread is not None
            or self._links_thread is not None or self._shutting_down
        ):
            return
        try:
            proxy = self._proxy_provider()
        except config.ConfigError as error:
            self.state_label.setText("Ошибка прокси")
            self.run_status.setText(str(error))
            return
        thread = BrowserWalkThread(
            self.site, installation, self._product_links, self.pause.value(), self,
            proxy=proxy, ftp_settings=self.settings()["ftp"], headless=headless,
        )
        self._walk_thread = thread
        self._clear_ozon_results()
        self._clear_wildberries_results()
        self._clear_yandex_results()
        self._saved_images = {}
        self.ftp_status.setText("Ожидание завершения сбора данных…")
        self.state_label.setText("Запуск…")
        self.run_status.setText(
            f"Запускается {installation.name}"
            f"{' в фоновом режиме' if headless else ''}; "
            f"заданий в очереди: {len(self._product_links) * 3}."
        )
        thread.page_started.connect(self._walk_page_started)
        thread.page_loaded.connect(self._walk_page_loaded)
        thread.page_failed.connect(self._walk_page_failed)
        thread.ozon_parsed.connect(self._walk_ozon_parsed)
        thread.wildberries_parsed.connect(self._walk_wildberries_parsed)
        thread.yandex_parsed.connect(self._walk_yandex_parsed)
        thread.image_failed.connect(self._walk_image_failed)
        thread.ozon_recovery_started.connect(self._walk_ozon_recovery_started)
        thread.ozon_direct_started.connect(self._walk_ozon_direct_started)
        thread.yandex_direct_started.connect(self._walk_yandex_direct_started)
        thread.ftp_upload_started.connect(self._walk_ftp_upload_started)
        thread.browser_notice.connect(self._walk_browser_notice)
        thread.finished.connect(self._walk_finished)
        thread.start()
        self._update_links_controls()
        self.walk_state_changed.emit()

    def _stop_walk(self) -> None:
        thread = self._walk_thread
        if thread is None:
            return
        thread.stop()
        self.state_label.setText("Остановка…")
        self.run_status.setText("Ожидание завершения текущей загрузки…")
        self._update_browser_controls()

    @Slot(str, str, str, int, int)
    def _walk_page_started(self, product_id: str, marketplace: str, url: str, index: int, total: int) -> None:
        if self._shutting_down:
            return
        self.state_label.setText(f"Обход {index}/{total}")
        action = (
            "Запрашивается JSON Wildberries"
            if marketplace == "Wildberries" else f"Открывается {marketplace}"
        )
        self.run_status.setText(f"{action}, товар {product_id}: {_soft_wrap(url)}")
        row = (index - 1) // 3
        if row < self.links_preview.rowCount():
            self.links_preview.setCurrentCell(row, (index - 1) % 3 + 1)

    @Slot(str, str, int, int, int, bool)
    def _walk_page_loaded(
        self, product_id: str, marketplace: str, index: int, total: int, status: int,
        direct: bool,
    ) -> None:
        if not self._shutting_down:
            if marketplace == "Wildberries":
                detail = f"JSON: {len(self._wildberries_results.get(product_id, ()))} товаров"
            elif marketplace == "Ozon":
                access_detail = (
                    "страница открылась после проверки браузера (первичный HTTP 403)"
                    if status == 403 else f"HTTP {status}"
                )
                detail = f"карточек: {len(self._ozon_results.get(product_id, ()))}; {access_detail}"
            else:
                detail = f"карточек: {len(self._yandex_results.get(product_id, ()))}; HTTP {status}"
            if direct:
                detail += "; без прокси"
            self.run_status.setText(f"Загружено {index}/{total}: {marketplace}, товар {product_id}, {detail}.")

    @Slot(str)
    def _walk_browser_notice(self, message: str) -> None:
        if not self._shutting_down:
            self.log.append_error("", "", f"Браузер: {message}")

    @Slot(str)
    def _walk_ozon_recovery_started(self, product_id: str) -> None:
        if not self._shutting_down:
            self.run_status.setText(
                f"Ozon показал капчу для товара {product_id}; повторная попытка в новом профиле браузера…"
            )

    @Slot(str)
    def _walk_ozon_direct_started(self, product_id: str) -> None:
        if not self._shutting_down:
            self.run_status.setText(
                f"Ozon показал капчу для товара {product_id} и в новом профиле; "
                "повторная попытка без прокси…"
            )

    @Slot(str)
    def _walk_yandex_direct_started(self, product_id: str) -> None:
        if not self._shutting_down:
            self.run_status.setText(
                f"Яндекс Маркет не открылся через прокси для товара {product_id}; "
                "повторная попытка без прокси…"
            )

    @Slot()
    def _walk_ftp_upload_started(self) -> None:
        if not self._shutting_down:
            self.ftp_status.setText("Загружаются фотографии и JSON на FTP…")
            self.run_status.setText("Сбор завершён; идёт выгрузка на FTP…")

    def _show_parsed_products(
        self, product_id: str, products: tuple[_Product, ...],
        results: dict[str, tuple[_Product, ...]],
        table: MarketplaceResultsTable, hint: QLabel,
    ) -> None:
        if self._shutting_down:
            return
        results[product_id] = products
        table.append_products(product_id, products)
        hint.setText(
            f"Собрано товаров: {sum(len(items) for items in results.values())}; "
            f"показано: {table.rowCount()}."
        )

    @Slot(str, object)
    def _walk_ozon_parsed(
        self, product_id: str, products: tuple[OzonProduct, ...],
    ) -> None:
        self._show_parsed_products(
            product_id, products, self._ozon_results, self.ozon_table, self.ozon_hint,
        )

    @Slot(str, object)
    def _walk_wildberries_parsed(
        self, product_id: str, products: tuple[WildberriesProduct, ...],
    ) -> None:
        self._show_parsed_products(
            product_id, products, self._wildberries_results,
            self.wildberries_table, self.wildberries_hint,
        )

    @Slot(str, object)
    def _walk_yandex_parsed(
        self, product_id: str, products: tuple[YandexMarketProduct, ...],
    ) -> None:
        self._show_parsed_products(
            product_id, products, self._yandex_results, self.yandex_table,
            self.yandex_hint,
        )

    @Slot(str, str, str, str)
    def _walk_image_failed(
        self, product_id: str, marketplace: str, image_url: str, message: str,
    ) -> None:
        if not self._shutting_down:
            self.log.append_error(product_id, image_url, f"{marketplace}: {message}")

    @Slot(str, str, str, int, int, str)
    def _walk_page_failed(
        self, product_id: str, marketplace: str, url: str, index: int, total: int, message: str,
    ) -> None:
        if not self._shutting_down:
            self.run_status.setText(f"Ошибка {index}/{total}: {marketplace}, товар {product_id}.")
            self.log.append_error(product_id, url, f"{marketplace}: {message}")

    @Slot()
    def _walk_finished(self) -> None:
        thread = self._walk_thread
        if thread is None:
            return
        self._walk_thread = None
        self._saved_images = thread.saved_images.copy()
        if not self._shutting_down:
            if thread.ftp_result is not None:
                result = thread.ftp_result
                self.ftp_status.setText(
                    f"Выгружено: {result.products} ID, {result.uploaded_images} фото; "
                    f"JSON: {_soft_wrap(result.remote_json)}"
                )
            elif thread.ftp_error is not None:
                self.ftp_status.setText(f"Ошибка выгрузки: {thread.ftp_error}")
                self.log.append_error("", "", f"FTP: {thread.ftp_error}")
            elif thread.cancelled:
                self.ftp_status.setText("Выгрузка не завершена: обход остановлен.")
            else:
                self.ftp_status.setText("FTP не настроен; данные остались локально.")
            if thread.error:
                self.state_label.setText("Ошибка")
                self.run_status.setText(f"Не удалось выполнить обход: {thread.error}")
                self.log.append_error("", "", f"Обход страниц: {thread.error}")
            else:
                self.state_label.setText("Остановлен" if thread.cancelled else "Завершён")
                ending = "Остановлено" if thread.cancelled else "Обход завершён"
                self.run_status.setText(
                    f"{ending}. Загружено: {thread.loaded}; ошибок: {thread.failed}; "
                    f"товаров Ozon: {sum(len(items) for items in self._ozon_results.values())}; "
                    f"товаров Wildberries: {sum(len(items) for items in self._wildberries_results.values())}; "
                    f"товаров Яндекс Маркета: {sum(len(items) for items in self._yandex_results.values())}; "
                    f"фото: {sum(len(items) for items in self._saved_images.values())}; "
                    f"ошибок фото: {thread.image_failures}."
                    + (
                        f" FTP: {thread.ftp_result.uploaded_images} фото и JSON выгружены."
                        if thread.ftp_result is not None else
                        " FTP: ошибка выгрузки."
                        if thread.ftp_error is not None else ""
                    )
                )
            self._update_links_controls()
        thread.deleteLater()
        self.walk_state_changed.emit()
        self.walk_finished.emit()

    def resizeEvent(self, event) -> None:
        # QScrollArea may keep the page wider than its viewport to satisfy a
        # two-column minimum width; use the visible width to break that cycle.
        viewport = self.parentWidget()
        compact = (viewport.width() if viewport is not None else self.width()) < 1200
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

    def _toggle_source_schedule(self, enabled: bool) -> None:
        for widget in (
            self.source_schedule_time, self.source_schedule_interval, self.source_schedule_unit,
        ):
            widget.setEnabled(enabled)

    def _configure_source_schedule(self) -> None:
        schedule = None
        if self.source_schedule_enabled.isChecked():
            qtime = self.source_schedule_time.time()
            schedule = Schedule(
                time(qtime.hour(), qtime.minute()), self.source_schedule_interval.value(),
                str(self.source_schedule_unit.currentData()),
            )
        self.scheduler.configure_source(schedule)

    def _configure_analysis_schedule(self) -> None:
        schedule = None
        if self.schedule_enabled.isChecked():
            qtime = self.start_time.time()
            schedule = Schedule(
                time(qtime.hour(), qtime.minute()), self.interval.value(),
                str(self.interval_unit.currentData()),
            )
        else:
            self._pending_scheduled_walk = False
        self.scheduler.configure_analysis(schedule)

    def _update_schedule_status(self) -> None:
        source = self.scheduler.next_source
        analysis = self.scheduler.next_analysis
        self.source_next_label.setText(
            f"Следующая загрузка: {source:%d.%m.%Y %H:%M}." if source else
            "Следующая загрузка: выключена."
        )
        self.analysis_next_label.setText(
            f"Следующий анализ: {analysis:%d.%m.%Y %H:%M}." if analysis else
            "Следующий анализ: выключен."
        )

    def _scheduled_source_due(self) -> None:
        if self._shutting_down:
            return
        if self._links_thread is not None or self._walk_thread is not None:
            self.log.append_error("", "", "Расписание JSON: предыдущая операция ещё идёт; запуск пропущен.")
            return
        if not self.source_url.text().strip() or not self.token.text():
            self.log.append_error("", "", "Расписание JSON: укажите URL источника и токен.")
            return
        self._fetch_product_links()

    def _scheduled_analysis_due(self) -> None:
        if self._shutting_down:
            return
        if self._walk_thread is not None:
            self.log.append_error("", "", "Расписание анализа: предыдущий обход ещё идёт; запуск пропущен.")
            return
        if self._selected_installation() is None:
            self.log.append_error("", "", "Расписание анализа: выбранный браузер не найден.")
            return
        if self._links_thread is not None:
            self._pending_scheduled_walk = True
            self.run_status.setText("Анализ по расписанию ожидает загрузки ссылок…")
            return
        if not self._product_links:
            if not self.source_url.text().strip() or not self.token.text():
                self.log.append_error("", "", "Расписание анализа: укажите URL источника и токен.")
                return
            self._pending_scheduled_walk = True
            self._fetch_product_links()
            return
        self._begin_walk(headless=True)
        if self._walk_thread is None:
            self.log.append_error("", "", "Расписание анализа: запуск обхода не удался.")

    def settings(self) -> dict:
        """Return the settings currently shown on this site's tab."""
        return {
            "site": self.site,
            "source_url": self.source_url.text().strip(),
            "token": self.token.text(),
            "source_schedule_enabled": self.source_schedule_enabled.isChecked(),
            "source_schedule_time": self.source_schedule_time.time().toString("HH:mm"),
            "source_schedule_interval": self.source_schedule_interval.value(),
            "source_schedule_unit": self.source_schedule_unit.currentData(),
            "browser": self._selected_browser_name(),
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
        self.source_schedule_enabled.setChecked(settings.get("source_schedule_enabled", False))
        schedule = settings["schedule"]
        self.source_schedule_time.setTime(QTime.fromString(
            settings.get("source_schedule_time", schedule["start_time"]), "HH:mm",
        ))
        self.source_schedule_interval.setValue(
            settings.get("source_schedule_interval", schedule["interval"])
        )
        self.source_schedule_unit.setCurrentIndex(self.source_schedule_unit.findData(
            settings.get("source_schedule_unit", schedule["unit"])
        ))
        self.browser.setCurrentIndex(self.browser.findData(settings["browser"]))
        self.pause.setValue(settings["pause_seconds"])
        ftp = settings["ftp"]
        self.ftp_host.setText(ftp["host"])
        self.ftp_port.setValue(ftp["port"])
        self.ftp_user.setText(ftp["username"])
        self.ftp_password.setText(ftp["password"])
        self.remote_path.setText(ftp["remote_path"])
        self.schedule_enabled.setChecked(schedule["enabled"])
        self.start_time.setTime(QTime.fromString(schedule["start_time"], "HH:mm"))
        self.interval.setValue(schedule["interval"])
        self.interval_unit.setCurrentIndex(self.interval_unit.findData(schedule["unit"]))
