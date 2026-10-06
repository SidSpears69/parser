"""Qt6 user interface for Web Parser."""

from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, quote_plus

from PySide6.QtCore import QPointF, Qt, QThread, QTime, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from web_parser import config
from web_parser.browser_discovery import BrowserInstallation, discover_browsers
from web_parser.browser_session import BrowserError, BrowserSessionManager
from web_parser.product_links import ProductLinks, ProductLinksError, fetch_product_links


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


def _paint_arrow_control(
    widget: QWidget,
    event: QPaintEvent,
    native_paint: Callable[[QPaintEvent], None],
    *,
    spin: bool,
) -> None:
    """Paint the native control and add a visible chevron on top."""
    native_paint(event)
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#36577f" if widget.isEnabled() else "#9aaabd")
    painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    x = widget.width() - (12 if spin else 14)
    middle = widget.height() / 2
    if spin:
        painter.drawPolyline((QPointF(x - 4, middle / 2 + 2), QPointF(x, middle / 2 - 2), QPointF(x + 4, middle / 2 + 2)))
        painter.drawPolyline((QPointF(x - 4, middle + middle / 2 - 2), QPointF(x, middle + middle / 2 + 2), QPointF(x + 4, middle + middle / 2 - 2)))
    else:
        painter.drawPolyline((QPointF(x - 4, middle - 2), QPointF(x, middle + 2), QPointF(x + 4, middle - 2)))


class ReadableSpinBox(QSpinBox):
    """Spin box with visible chevrons independent of the native Qt style."""

    def paintEvent(self, event) -> None:
        _paint_arrow_control(self, event, super().paintEvent, spin=True)


class ReadableTimeEdit(QTimeEdit):
    """Time editor with visible chevrons independent of the native Qt style."""

    def paintEvent(self, event) -> None:
        _paint_arrow_control(self, event, super().paintEvent, spin=True)


class ReadableComboBox(QComboBox):
    """Combo box with a visible down chevron independent of the native Qt style."""

    def paintEvent(self, event) -> None:
        _paint_arrow_control(self, event, super().paintEvent, spin=False)


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
        self.save_button.setObjectName("saveLog")
        self.clear_button = QPushButton("Очистить")
        self.clear_button.setObjectName("clearLog")
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


class BrowserLaunchThread(QThread):
    """Run the potentially slow driver lookup and browser start off the UI thread."""

    def __init__(self, manager: BrowserSessionManager, name: str, binary: Path, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.name = name
        self.binary = binary
        self.description: str | None = None
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.description = self.manager.launch(self.name, self.binary)
        except BrowserError as error:
            self.error = str(error) or type(error).__name__


class ProductLinksFetchThread(QThread):
    """Fetch the site's source JSON without blocking the Qt event loop."""

    def __init__(self, source_url: str, token: str, parent=None):
        super().__init__(parent)
        self.source_url = source_url
        self.token = token
        self.links: list[ProductLinks] | None = None
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.links = fetch_product_links()
        except ProductLinksError as error:
            message = str(error) or type(error).__name__
            # Avoid exposing the credential even if a lower layer includes it
            # in an error message or in an URL-encoded query parameter.
            for secret in {self.token, quote(self.token, safe=""), quote_plus(self.token)}:
                if secret:
                    message = message.replace(secret, "[токен скрыт]")
            self.error = message


class SitePage(QWidget):
    start_requested = Signal(str)
    stop_requested = Signal(str)
    refresh_browsers_requested = Signal()
    browser_launch_finished = Signal()
    links_fetch_finished = Signal()

    def __init__(self, site: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.site = site
        self._available_browsers: dict[str, BrowserInstallation] = {}
        self._browser_manager: BrowserSessionManager | None = None
        self._launch_thread: BrowserLaunchThread | None = None
        self._links_thread: ProductLinksFetchThread | None = None
        self._product_links: tuple[ProductLinks, ...] = ()
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
        self.remote_path = field("/site/parser/", "remotePath")
        ftp_form.addRow("Сервер", connection)
        ftp_form.addRow("Логин / пароль", credentials)
        ftp_form.addRow("Каталог JSON", self.remote_path)
        ftp_layout.addLayout(ftp_form)
        grid.addWidget(ftp, 0, 1)

        browser, browser_layout = section("Браузер и обработка")
        browser_form = form()
        self.browser = ReadableComboBox()
        self.browser.setObjectName("browser")
        for browser_name in BROWSERS:
            self.browser.addItem(browser_name, browser_name)
        self.browser.setToolTip("Выберите установленный браузер для проверки запуска.")
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
        self.browser_path_label.setWordWrap(True)
        browser_layout.addWidget(self.browser_path_label)
        browser_actions = QHBoxLayout()
        self.launch_browser_button = QPushButton("Запустить браузер")
        self.launch_browser_button.setObjectName("launchBrowser")
        self.launch_browser_button.setEnabled(False)
        self.close_browser_button = QPushButton("Закрыть браузер")
        self.close_browser_button.setObjectName("closeBrowser")
        self.close_browser_button.setEnabled(False)
        browser_actions.addWidget(self.launch_browser_button)
        browser_actions.addWidget(self.close_browser_button)
        browser_actions.addStretch()
        browser_layout.addLayout(browser_actions)
        self.browser_launch_status = label("Браузер не запущен.", "hint")
        self.browser_launch_status.setWordWrap(True)
        browser_layout.addWidget(self.browser_launch_status)
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
        self.start_time = ReadableTimeEdit(QTime(22, 0))
        self.start_time.setObjectName("startTime")
        self.start_time.setDisplayFormat("HH:mm")
        self.interval = ReadableSpinBox()
        self.interval.setObjectName("interval")
        self.interval.setRange(1, 999)
        self.interval.setValue(1)
        self.interval_unit = ReadableComboBox()
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

        links_panel, links_layout = section("Ссылки товаров")
        self.links_preview = QTableWidget(0, 4)
        self.links_preview.setObjectName("linksPreview")
        self.links_preview.setHorizontalHeaderLabels(
            ("ID", "Ozon", "Wildberries", "Яндекс Маркет")
        )
        self.links_preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.links_preview.verticalHeader().setVisible(False)
        self.links_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.links_preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.links_preview.setAlternatingRowColors(True)
        self.links_preview.setMinimumHeight(160)
        links_layout.addWidget(self.links_preview)
        self.links_preview_hint = label("Загрузите JSON, чтобы увидеть ID и ссылки товаров.", "hint")
        links_layout.addWidget(self.links_preview_hint)
        layout.addWidget(links_panel)

        self.log = ErrorLog(site)
        layout.addWidget(self.log, 1)
        self.start_button.clicked.connect(lambda: self.start_requested.emit(self.site))
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self.site))
        self.browser.currentIndexChanged.connect(self._update_browser_controls)
        self.refresh_browsers_button.clicked.connect(lambda: self.refresh_browsers_requested.emit())
        self.launch_browser_button.clicked.connect(self._launch_browser)
        self.close_browser_button.clicked.connect(self._close_browser)
        self.source_url.textChanged.connect(self._clear_product_links)
        self.token.textChanged.connect(self._clear_product_links)
        self.load_links_button.clicked.connect(self._fetch_product_links)

    @property
    def product_links(self) -> tuple[ProductLinks, ...]:
        """Current parsed links for this site, in source order."""
        return self._product_links

    @property
    def links_fetch_in_progress(self) -> bool:
        return self._links_thread is not None and self._links_thread.isRunning()

    @property
    def links_fetch_pending(self) -> bool:
        return self._links_thread is not None

    def _clear_product_links(self) -> None:
        if self._links_thread is None:
            self._product_links = ()
            self.links_preview.setRowCount(0)
            self.links_status.setText("Ссылки ещё не загружены.")
            self.links_preview_hint.setText("Загрузите JSON, чтобы увидеть ID и ссылки товаров.")
        self._update_links_controls()

    def _update_links_controls(self) -> None:
        fetching = self._links_thread is not None
        self.source_url.setEnabled(not fetching)
        self.token.setEnabled(not fetching)
        self.load_links_button.setEnabled(
            bool(self.source_url.text().strip() and self.token.text())
            and not fetching and not self._shutting_down
        )

    def _fetch_product_links(self) -> None:
        if self._links_thread is not None:
            return
        source_url = self.source_url.text().strip()
        token = self.token.text()
        if not source_url or not token:
            return
        self._product_links = ()
        self.links_preview.setRowCount(0)
        self.links_preview_hint.setText("Ожидание ответа источника…")
        self.links_status.setText("Загрузка JSON и разбор ссылок…")
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
        elif thread.error is not None or thread.links is None:
            message = thread.error or "Загрузка прервана из-за внутренней ошибки."
            self.links_status.setText(f"Не удалось загрузить ссылки: {message}")
            self.links_preview_hint.setText("Проверьте URL и токен доступа.")
            self.log.append_error("", "", f"Сбор ссылок: {message}")
        else:
            self._product_links = tuple(thread.links)
            self._show_product_links()
        self._update_links_controls()
        thread.token = ""
        thread.deleteLater()
        self.links_fetch_finished.emit()

    def _show_product_links(self) -> None:
        total = len(self._product_links)
        shown = min(total, 20)
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
                self.links_preview.setItem(row, column, item)
        self.links_status.setText(f"Загружено товаров: {total}")
        self.links_preview_hint.setText(
            f"Показаны первые {shown} из {total} товаров." if total > shown
            else "Все загруженные товары показаны в таблице." if total
            else "В источнике нет товаров."
        )

    def shutdown_links(self) -> None:
        """Release a completed fetch worker when the window closes."""
        self._shutting_down = True
        if self._links_thread is not None:
            self._links_thread.wait()

    @property
    def browser_launch_pending(self) -> bool:
        """Whether launch results still need to be processed by this page."""
        return self._launch_thread is not None

    @property
    def browser_launch_in_progress(self) -> bool:
        """Whether the browser launch worker is still running."""
        return self._launch_thread is not None and self._launch_thread.isRunning()

    def prepare_browser_shutdown(self) -> None:
        self._shutting_down = True

    def set_available_browsers(self, available: dict[str, BrowserInstallation]) -> None:
        """Refresh installation hints while retaining canonical config values."""
        self._available_browsers = available.copy()
        for index, browser_name in enumerate(BROWSERS):
            installation = available.get(browser_name)
            suffix = "найден" if installation else "не найден"
            self.browser.setItemText(index, f"{browser_name} — {suffix}")
            self.browser.setItemData(
                index, str(installation.path) if installation else "", Qt.ItemDataRole.ToolTipRole
            )
        self._update_browser_controls()

    def _selected_browser_name(self) -> str:
        return str(self.browser.currentData())

    def _update_browser_controls(self) -> None:
        name = self._selected_browser_name()
        installation = self._available_browsers.get(name)
        path_text = f"Путь: {installation.path}" if installation else "Установленный браузер не найден."
        self.browser_path_label.setText(path_text)
        self.browser_path_label.setToolTip(str(installation.path) if installation else "")
        starting = self._launch_thread is not None and self._launch_thread.isRunning()
        running = self._browser_manager is not None and self._browser_manager.is_running
        self.browser.setEnabled(not starting and not running)
        self.refresh_browsers_button.setEnabled(not starting and not running)
        self.launch_browser_button.setEnabled(installation is not None and not starting and not running)
        self.close_browser_button.setEnabled(running and not starting)

    def _launch_browser(self) -> None:
        name = self._selected_browser_name()
        installation = self._available_browsers.get(name)
        if installation is None or self._launch_thread is not None:
            return
        self._browser_manager = BrowserSessionManager()
        thread = BrowserLaunchThread(self._browser_manager, name, installation.path, self)
        self._launch_thread = thread
        self.browser_launch_status.setText(f"Запускается {name}; подбор драйвера может занять время…")
        thread.finished.connect(self._browser_launch_finished)
        thread.start()
        self._update_browser_controls()

    @Slot()
    def _browser_launch_finished(self) -> None:
        thread = self._launch_thread
        if thread is None:
            return
        self._launch_thread = None
        if self._shutting_down:
            if self._browser_manager is not None:
                try:
                    self._browser_manager.close()
                except BrowserError:
                    pass
            thread.deleteLater()
            self.browser_launch_finished.emit()
            return
        if thread.error or thread.description is None:
            error_message = thread.error or "Запуск прерван из-за внутренней ошибки."
            if self._browser_manager is not None:
                try:
                    self._browser_manager.close()
                except BrowserError:
                    pass
            self.browser_launch_status.setText(f"Не удалось запустить браузер: {error_message}")
            self.log.append_error("", "", f"Запуск браузера: {error_message}")
        else:
            self.browser_launch_status.setText(f"Браузер запущен. {thread.description}")
        self._update_browser_controls()
        thread.deleteLater()
        self.browser_launch_finished.emit()

    def _close_browser(self) -> None:
        if self._launch_thread is not None or self._browser_manager is None:
            return
        try:
            self._browser_manager.close()
        except BrowserError as error:
            self.browser_launch_status.setText(f"Не удалось закрыть браузер: {error}")
            self.log.append_error("", "", f"Закрытие браузера: {error}")
        else:
            self.browser_launch_status.setText("Браузер закрыт.")
        self._update_browser_controls()

    def shutdown_browser(self) -> None:
        """Keep the worker alive until launch completes, then release its session."""
        self.prepare_browser_shutdown()
        if self._launch_thread is not None:
            self._launch_thread.wait()
        if self._browser_manager is not None:
            try:
                self._browser_manager.close()
            except BrowserError:
                pass

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

    def settings(self) -> dict:
        """Return the settings currently shown on this site's tab."""
        return {
            "site": self.site,
            "source_url": self.source_url.text().strip(),
            "token": self.token.text(),
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
        self.browser.setCurrentIndex(self.browser.findData(settings["browser"]))
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
        self.load_config_button.setObjectName("loadConfig")
        self.save_config_button = QPushButton("Сохранить…")
        self.save_config_button.setObjectName("saveConfig")
        self.load_config_button.setToolTip("Загрузить настройки всех вкладок из файла")
        self.save_config_button.setToolTip("Сохранить настройки всех вкладок в файл")
        config_layout.addWidget(self.load_config_button)
        config_layout.addWidget(self.save_config_button)
        layout.addLayout(config_layout)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("siteTabs")
        self.tabs.setDocumentMode(True)
        self._browsers_scanned = False
        self._close_pending = False
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
            page.refresh_browsers_requested.connect(self._refresh_browsers)
            page.browser_launch_finished.connect(self._resume_close)
            page.links_fetch_finished.connect(self._resume_close)
        layout.addWidget(self.tabs, 1)
        self.save_config_button.clicked.connect(self._choose_save_config)
        self.load_config_button.clicked.connect(self._choose_load_config)
        self._refresh_browsers()

    @Slot()
    def _refresh_browsers(self) -> None:
        available = discover_browsers()
        for page in self.pages.values():
            page.set_available_browsers(available)
            if not self._browsers_scanned and available and page.settings()["browser"] not in available:
                first_installed = next(iter(available))
                page.browser.setCurrentIndex(page.browser.findData(first_installed))
        self._browsers_scanned = True
        self.statusBar().showMessage(f"Найдено браузеров: {len(available)}")

    def closeEvent(self, event) -> None:
        busy = [
            page for page in self.pages.values()
            if page.browser_launch_pending or page.links_fetch_pending
        ]
        if busy:
            event.ignore()
            if not self._close_pending:
                self._close_pending = True
                self.statusBar().showMessage("Завершаются фоновые операции перед закрытием приложения…")
                self.setEnabled(False)
                for page in self.pages.values():
                    page.prepare_browser_shutdown()
            return
        for page in self.pages.values():
            page.shutdown_browser()
            page.shutdown_links()
        super().closeEvent(event)

    @Slot()
    def _resume_close(self) -> None:
        if self._close_pending and all(
            not page.browser_launch_pending and not page.links_fetch_pending
            for page in self.pages.values()
        ):
            self._close_pending = False
            QTimer.singleShot(0, self.close)

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

    @Slot(str)
    def _collection_unavailable(self, site: str) -> None:
        self.statusBar().showMessage(f"{site}: модуль сбора ещё не подключён. Запуск не выполнен.")
