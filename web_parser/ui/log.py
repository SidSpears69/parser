"""Per-site error log widget."""

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, quote_plus

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from web_parser.error_file import ErrorFile
from web_parser.ui.widgets import label


_HIDDEN = "[скрыто]"


def _redact(text: str, secrets: Iterable[str]) -> str:
    variants = set()
    for secret in secrets:
        if secret:
            variants.update((secret, quote(secret, safe=""), quote_plus(secret, safe="")))
    for variant in sorted(variants, key=len, reverse=True):
        text = text.replace(variant, _HIDDEN)
    return text


class ErrorLog(QWidget):
    """Bounded, per-site error log. Safe to append through a queued Qt signal."""

    error_received = Signal(str, str, str)

    def __init__(
        self, site: str, parent: QWidget | None = None, *,
        log_root: Path | None = None,
        secret_provider: Callable[[], Iterable[str]] = lambda: (),
    ):
        super().__init__(parent)
        self.site = site
        self.file = ErrorFile(site, log_root)
        self._secret_provider = secret_provider
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
        self.file_label = label(f"Файл: artifacts/logs/{site}.log", "hint")
        self.file_label.setToolTip(str(self.file.path))
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)
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
        try:
            previous = self.file.recent()
        except OSError:
            self.file_label.setText("Не удалось прочитать файл журнала ошибок.")
        else:
            if previous:
                self.editor.setPlainText("\n".join(previous))
        self.save_button.clicked.connect(self._choose_export)
        self.clear_button.setToolTip("Очистить записи в окне; файл журнала сохранится")
        self.clear_button.clicked.connect(self.editor.clear)
        self.editor.textChanged.connect(self._refresh)
        self.error_received.connect(self.append_error)
        self._refresh()

    @Slot(str, str, str)
    def append_error(self, product_id: str, url: str, message: str) -> None:
        # One event per line keeps the bounded log and its counter consistent.
        secrets = tuple(self._secret_provider())

        def clean(value: object) -> str:
            return _redact(" ".join(str(value).split()), secrets)

        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        line = (
            f"[{timestamp}] ID: {clean(product_id) or '—'} | "
            f"URL: {clean(url) or '—'} | {clean(message)}"
        )
        self.editor.appendPlainText(line)
        try:
            self.file.append(line)
        except OSError:
            self.file_label.setText("Не удалось записать файл журнала ошибок.")

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
