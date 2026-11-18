"""Main window and cross-site configuration controls."""

from datetime import datetime
from pathlib import Path
import subprocess

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from web_parser import config
from web_parser.browser_discovery import discover_browsers
from web_parser.email_notifications import EmailNotifier
from web_parser.paths import ARTIFACT_ROOT
from web_parser.ui.site import SitePage
from web_parser.ui.update import UpdateThread
from web_parser.ui.widgets import (
    ClickableCursorFilter, ReadableComboBox, ReadableSpinBox, field, form, label, section,
)
from web_parser.updates import Release, installed_version, is_newer

SITES = config.SITES


class MainWindow(QMainWindow):
    def __init__(self, *, log_root: Path | None = None):
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
        self.update_button = QPushButton("Проверить обновления")
        self.update_button.setObjectName("checkUpdates")
        header.addWidget(self.update_button)
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

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("settingsScroll")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_container = QWidget()
        self.settings_container.setObjectName("settingsContainer")
        settings_layout = QVBoxLayout(self.settings_container)
        self._settings_layout = settings_layout
        settings_layout.setContentsMargins(16, 16, 16, 16)
        settings_layout.setSpacing(16)
        self.settings_scroll.setWidget(self.settings_container)
        settings_layout.addLayout(config_layout)

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
        settings_layout.addWidget(self.proxy_section)

        self.email_section, email_layout = section("Уведомления по почте")
        self.email_enabled = QCheckBox("Отправлять письма администратору")
        email_header = QHBoxLayout()
        email_header.addWidget(self.email_enabled)
        email_header.addStretch()
        self.email_settings_button = QPushButton("Параметры SMTP…")
        self.email_settings_button.setCheckable(True)
        email_header.addWidget(self.email_settings_button)
        email_layout.addLayout(email_header)
        self.email_details = QWidget()
        email_details_layout = QVBoxLayout(self.email_details)
        email_details_layout.setContentsMargins(0, 0, 0, 0)
        email_form = form()
        email_form.setHorizontalSpacing(12)
        email_form.setVerticalSpacing(12)
        self.email_host = field("smtp.example.ru", "emailHost")
        self.email_port = ReadableSpinBox()
        self.email_port.setObjectName("emailPort")
        self.email_port.setRange(1, 65535)
        self.email_port.setValue(465)
        self.email_port.setFixedWidth(90)
        self.email_port.setAccessibleName("Порт SMTP")
        self.email_security = ReadableComboBox()
        self.email_security.setObjectName("emailSecurity")
        self.email_security.addItem("SSL/TLS", "ssl")
        self.email_security.addItem("STARTTLS", "starttls")
        self.email_security.setAccessibleName("Защита SMTP")
        server_row = QHBoxLayout()
        server_row.setSpacing(12)
        server_row.addWidget(self.email_host, 1)
        server_row.addWidget(self.email_port)
        server_row.addWidget(self.email_security)
        self.email_username = field("Логин SMTP", "emailUsername")
        self.email_password = field("Пароль SMTP", "emailPassword", secret=True)
        credentials = QHBoxLayout()
        credentials.setSpacing(12)
        credentials.addWidget(self.email_username)
        credentials.addWidget(self.email_password)
        self.email_sender = field("parser@example.ru", "emailSender")
        self.email_recipient = field("Первый адрес", "emailRecipient")
        self.email_recipient2 = field("Второй адрес (необязательно)", "emailRecipient2")
        self.email_sender.setAccessibleName("Адрес отправителя")
        self.email_recipient.setAccessibleName("Первый адрес получателя")
        self.email_recipient2.setAccessibleName("Второй адрес получателя")
        recipients = QHBoxLayout()
        recipients.setSpacing(12)
        recipients.addWidget(self.email_recipient)
        recipients.addWidget(self.email_recipient2)
        email_form.addRow("SMTP", server_row)
        email_form.addRow("Авторизация", credentials)
        email_form.addRow("Отправитель", self.email_sender)
        email_form.addRow("Получатели", recipients)
        email_details_layout.addLayout(email_form)
        self.email_details.setMinimumHeight(self.email_details.sizeHint().height())
        email_layout.addWidget(self.email_details)
        self.email_settings_button.toggled.connect(self._toggle_email_details)
        self.email_details.setVisible(False)
        email_actions = QHBoxLayout()
        self.email_test_button = QPushButton("Отправить тест")
        self.email_status = label("Уведомления выключены.", "hint")
        self.email_status.setWordWrap(True)
        email_actions.addWidget(self.email_test_button)
        email_actions.addWidget(self.email_status)
        email_layout.addLayout(email_actions)
        settings_layout.addWidget(self.email_section)
        self.email_notifier = EmailNotifier()
        self.email_notifier.sent.connect(self._email_sent)
        self.email_notifier.failed.connect(self._email_failed)
        self.email_enabled.toggled.connect(self._configure_email)
        for widget in (self.email_host, self.email_username, self.email_password,
                       self.email_sender, self.email_recipient, self.email_recipient2):
            widget.editingFinished.connect(self._configure_email)
        self.email_port.valueChanged.connect(self._configure_email)
        self.email_security.currentIndexChanged.connect(self._email_security_changed)
        self.email_test_button.clicked.connect(self._test_email)
        self._configure_email()

        self._halt_file = (log_root if log_root is not None else ARTIFACT_ROOT) / "layout-halt.flag"
        self._emergency_stopped = self._halt_file.exists()
        self.halt_banner = label("", "hint")
        self.resume_button = QPushButton("Снять аварийную остановку")
        self.resume_button.clicked.connect(self._clear_emergency_stop)
        halt_row = QHBoxLayout()
        halt_row.addWidget(self.halt_banner)
        halt_row.addStretch()
        halt_row.addWidget(self.resume_button)
        settings_layout.addLayout(halt_row)
        settings_layout.addStretch()
        self._update_halt_banner()
        self.settings_container.setMinimumHeight(self.settings_container.sizeHint().height())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("siteTabs")
        self.tabs.setDocumentMode(True)
        self._browsers_scanned = False
        self._close_pending = False
        self._update_thread: UpdateThread | None = None
        self._update_release: Release | None = None
        self._update_installer: str | None = None
        self._pending_installer: str | None = None
        self._update_error: str | None = None
        self._current_version: str | None = None
        self.pages: dict[str, SitePage] = {}
        for site in SITES:
            page = SitePage(
                site,
                proxy_provider=self._browser_proxy,
                proxy_secrets=lambda: (self.proxy_username.text(), self.proxy_password.text()),
                extra_secrets=lambda: (self.email_password.text(),),
                log_root=log_root,
            )
            self.pages[site] = page
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, site)
            page.refresh_browsers_requested.connect(self._refresh_browsers)
            page.walk_finished.connect(self._resume_close)
            page.walk_finished.connect(self._install_when_idle)
            page.walk_state_changed.connect(self._update_config_controls)
            page.links_fetch_finished.connect(self._resume_close)
            page.links_fetch_finished.connect(self._install_when_idle)
            page.links_fetch_finished.connect(self._update_config_controls)
            page.fatal_layout.connect(self._fatal_layout)
            page.log.event_recorded.connect(self._notify_email)
            if self._emergency_stopped:
                page.emergency_stop()
        self.main_tabs = QTabWidget()
        self.main_tabs.setObjectName("mainTabs")
        self.main_tabs.setDocumentMode(True)
        self.main_tabs.addTab(self.tabs, "Сайты")
        self.main_tabs.addTab(self.settings_scroll, "Общие настройки")
        layout.addWidget(self.main_tabs, 1)
        self.save_config_button.clicked.connect(self._choose_save_config)
        self.load_config_button.clicked.connect(self._choose_load_config)
        self.update_button.clicked.connect(self._check_updates)
        self._refresh_browsers()
        self._clickable_cursor_filter = ClickableCursorFilter(self)
        clickable_widgets = (
            *self.findChildren(QAbstractButton),
            *self.findChildren(QComboBox),
            self.tabs.tabBar(), self.main_tabs.tabBar(),
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
        update_running = self._update_thread is not None and self._update_thread.isRunning()
        if busy or update_running:
            event.ignore()
            if update_running and self._update_thread is not None:
                self._update_thread.requestInterruption()
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
        self.email_notifier.close()
        super().closeEvent(event)

    @Slot()
    def _resume_close(self) -> None:
        if self._close_pending and all(
                not page.walk_pending and not page.links_fetch_pending
                for page in self.pages.values()
        ) and (self._update_thread is None or not self._update_thread.isRunning()):
            self._close_pending = False
            QTimer.singleShot(0, self.close)

    def save_config_to(self, path: str | Path) -> None:
        config.save(
            path, {site: page.settings() for site, page in self.pages.items()},
            self.proxy_settings(), self.email_settings(),
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
        self._apply_email_settings(loaded.email)
        if self._emergency_stopped:
            for page in self.pages.values():
                page.emergency_stop()
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

    def email_settings(self) -> dict[str, str | int | bool]:
        return {
            "enabled": self.email_enabled.isChecked(),
            "host": self.email_host.text().strip(),
            "port": self.email_port.value(),
            "security": str(self.email_security.currentData()),
            "username": self.email_username.text(),
            "password": self.email_password.text(),
            "sender": self.email_sender.text().strip(),
            "recipient": self.email_recipient.text().strip(),
            "recipient2": self.email_recipient2.text().strip(),
        }

    def _apply_email_settings(self, settings: dict[str, str | int | bool]) -> None:
        self.email_host.setText(str(settings["host"]))
        self.email_security.setCurrentIndex(self.email_security.findData(settings["security"]))
        self.email_port.setValue(int(settings["port"]))
        self.email_username.setText(str(settings["username"]))
        self.email_password.setText(str(settings["password"]))
        self.email_sender.setText(str(settings["sender"]))
        self.email_recipient.setText(str(settings["recipient"]))
        self.email_recipient2.setText(str(settings.get("recipient2", "")))
        self.email_enabled.setChecked(bool(settings["enabled"]))
        self._configure_email()

    @Slot(bool)
    def _toggle_email_details(self, expanded: bool) -> None:
        self.email_details.setVisible(expanded)
        self.settings_container.setMinimumHeight(0)
        self.settings_scroll.updateGeometry()
        QTimer.singleShot(0, self._sync_settings_height)

    @Slot()
    def _sync_settings_height(self) -> None:
        self._settings_layout.activate()
        self.settings_container.setMinimumHeight(self.settings_container.sizeHint().height())
        if self.email_settings_button.isChecked():
            self.settings_scroll.verticalScrollBar().setValue(self.email_section.y())
        else:
            self.settings_scroll.verticalScrollBar().setValue(0)

    @Slot()
    def _configure_email(self) -> None:
        enabled = self.email_enabled.isChecked()
        for widget in (
            self.email_host, self.email_port, self.email_security, self.email_username,
            self.email_password, self.email_sender, self.email_recipient, self.email_recipient2,
        ):
            widget.setEnabled(enabled)
        self.email_test_button.setEnabled(enabled)
        try:
            self.email_notifier.configure(self.email_settings())
        except config.ConfigError:
            self.email_notifier.configure(config.default_email())
            self.email_status.setText("Укажите SMTP-сервер и адреса почты.")
        else:
            self.email_status.setText("Уведомления включены." if enabled else "Уведомления выключены.")

    @Slot()
    def _email_security_changed(self) -> None:
        if self.email_security.currentData() == "starttls" and self.email_port.value() == 465:
            self.email_port.setValue(587)
        elif self.email_security.currentData() == "ssl" and self.email_port.value() == 587:
            self.email_port.setValue(465)
        self._configure_email()

    @Slot()
    def _test_email(self) -> None:
        try:
            self.email_notifier.configure(self.email_settings())
        except config.ConfigError as error:
            self.email_notifier.configure(config.default_email())
            self.email_status.setText(str(error))
            return
        self.email_status.setText("Отправляется тестовое письмо…")
        self.email_notifier.submit("Web Parser: тестовое письмо для проверки почтовых уведомлений.")

    @Slot()
    def _email_sent(self) -> None:
        self.email_status.setText("Письмо принято SMTP-сервером.")

    @Slot(str)
    def _email_failed(self, reason: str) -> None:
        self.email_status.setText(f"Ошибка отправки письма: {reason}")

    @Slot(str, str)
    def _notify_email(self, site: str, line: str) -> None:
        self.email_notifier.submit(
            f"Web Parser | {site}\n{line}", critical="АВАРИЙНАЯ ОСТАНОВКА" in line,
        )

    @Slot(str, str, str, str, str)
    def _fatal_layout(
        self, site: str, product_id: str, marketplace: str, url: str, message: str,
    ) -> None:
        if self._emergency_stopped:
            return
        self._emergency_stopped = True
        try:
            self._halt_file.parent.mkdir(parents=True, exist_ok=True)
            self._halt_file.write_text(datetime.now().isoformat(), encoding="utf-8")
        except OSError:
            self.statusBar().showMessage("Не удалось сохранить аварийную остановку на диск.")
        for page in self.pages.values():
            page.emergency_stop()
        self._update_halt_banner()
        self.pages[site].log.append_error(
            product_id, url,
            f"{marketplace}: АВАРИЙНАЯ ОСТАНОВКА: {message} "
            "Основные и запасные селекторы не прочитали выдачу; все расписания выключены.",
        )

    @Slot()
    def _clear_emergency_stop(self) -> None:
        if any(page.walk_pending or page.links_fetch_pending for page in self.pages.values()):
            self.statusBar().showMessage("Дождитесь остановки текущих операций.")
            return
        try:
            self._halt_file.unlink(missing_ok=True)
        except OSError as error:
            QMessageBox.warning(self, "Не удалось снять остановку", str(error))
            return
        self._emergency_stopped = False
        for page in self.pages.values():
            page.clear_emergency_stop()
        self._update_halt_banner()
        self.statusBar().showMessage("Аварийная остановка снята. Расписания включите вручную.")

    def _update_halt_banner(self) -> None:
        self.halt_banner.setText(
            "Аварийная остановка: все запуски и расписания выключены."
            if self._emergency_stopped else ""
        )
        self.resume_button.setVisible(self._emergency_stopped)
        self.resume_button.setEnabled(
            self._emergency_stopped
            and not any(page.walk_pending or page.links_fetch_pending for page in getattr(self, "pages", {}).values())
        )

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
        self.email_section.setEnabled(editable)
        self.resume_button.setEnabled(self._emergency_stopped and editable)
        self.update_button.setEnabled(
            editable and self._update_thread is None and self._pending_installer is None
        )

    @Slot()
    def _check_updates(self) -> None:
        current = installed_version()
        if current is None:
            QMessageBox.information(
                self, "Обновления", "Обновление по кнопке доступно после установки выпуска приложения."
            )
            return
        if any(page.walk_pending or page.links_fetch_pending for page in self.pages.values()):
            QMessageBox.information(self, "Обновления", "Дождитесь завершения текущего прогона.")
            return
        self._current_version = current
        self._start_update_thread(None)

    def _start_update_thread(self, release: Release | None) -> None:
        self._update_release = None
        self._update_installer = None
        self._update_error = None
        thread = UpdateThread(release, self)
        self._update_thread = thread
        thread.release_found.connect(self._release_found)
        thread.installer_ready.connect(self._installer_ready)
        thread.download_progress.connect(self._download_progress)
        thread.failed.connect(self._update_failed)
        thread.finished.connect(self._update_finished)
        self.update_button.setEnabled(False)
        self.statusBar().showMessage(
            "Скачивание обновления…" if release is not None else "Проверка обновлений…"
        )
        thread.start()

    @Slot(object)
    def _release_found(self, release: Release) -> None:
        self._update_release = release

    @Slot(str)
    def _installer_ready(self, path: str) -> None:
        self._update_installer = path

    @Slot(int, int)
    def _download_progress(self, received: int, total: int) -> None:
        self.statusBar().showMessage(f"Скачивание обновления: {received * 100 // total}%")

    @Slot(str)
    def _update_failed(self, message: str) -> None:
        self._update_error = message

    @Slot()
    def _update_finished(self) -> None:
        thread = self._update_thread
        if thread is None:
            return
        was_download = thread.release is not None
        self._update_thread = None
        thread.deleteLater()
        if self._close_pending:
            self._resume_close()
            return
        self.update_button.setEnabled(True)
        if self._update_error:
            QMessageBox.warning(self, "Не удалось обновить приложение", self._update_error)
            return
        if was_download:
            if self._update_installer:
                self._launch_installer(self._update_installer)
            return
        release = self._update_release
        current = self._current_version
        if release is None or current is None:
            return
        if not is_newer(release, current):
            QMessageBox.information(self, "Обновления", f"Установлена последняя версия: {current}.")
            self.statusBar().showMessage("Обновлений нет")
            return
        answer = QMessageBox.question(
            self, "Доступно обновление",
            f"Версия {release.version} доступна (установлена {current}). "
            "Скачать и установить? Приложение закроется перед установкой.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_update_thread(release)

    def _launch_installer(self, path: str) -> None:
        if any(page.walk_pending or page.links_fetch_pending for page in self.pages.values()):
            self._pending_installer = path
            self.update_button.setEnabled(False)
            self.statusBar().showMessage("Обновление загружено; установка начнётся после прогона.")
            return
        try:
            subprocess.Popen([path, "/SP-", "/NORESTART", "/NORESTARTAPPLICATIONS"])
        except OSError as error:
            QMessageBox.warning(self, "Не удалось запустить установщик", str(error))
            return
        self.statusBar().showMessage("Установщик запущен. Приложение закрывается…")
        QTimer.singleShot(0, self.close)

    @Slot()
    def _install_when_idle(self) -> None:
        if self._pending_installer is None or self._close_pending:
            return
        path = self._pending_installer
        if any(page.walk_pending or page.links_fetch_pending for page in self.pages.values()):
            return
        self._pending_installer = None
        self._launch_installer(path)
