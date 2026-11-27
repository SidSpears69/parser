"""Three shared optional proxy slots in the general settings tab."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from web_parser import config
from web_parser.ui.widgets import field, form, label


class ProxyPoolEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.slots: list[tuple[QLineEdit, QLineEdit, QLineEdit]] = []
        for index in range(1, config.PROXY_SLOTS + 1):
            card = QFrame()
            card.setObjectName("proxySlot")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 12)
            card_layout.setSpacing(8)
            card_layout.addWidget(label(f"Прокси {index}", "sectionTitle"))
            fields = form()
            fields.setHorizontalSpacing(12)
            fields.setVerticalSpacing(10)
            server = field("socks5://адрес:порт", f"proxyServer{index}")
            username = field("Логин (если требуется)", f"proxyUsername{index}")
            password = field("Пароль (если требуется)", f"proxyPassword{index}", secret=True)
            server.setAccessibleName(f"Адрес прокси {index}")
            username.setAccessibleName(f"Логин прокси {index}")
            password.setAccessibleName(f"Пароль прокси {index}")
            credentials = QHBoxLayout()
            credentials.setSpacing(12)
            credentials.addWidget(username)
            credentials.addWidget(password)
            fields.addRow("Адрес", server)
            fields.addRow("Логин / пароль", credentials)
            card_layout.addLayout(fields)
            layout.addWidget(card)
            self.slots.append((server, username, password))
            for widget in (server, username, password):
                widget.textChanged.connect(self.changed)

    def settings(self) -> list[dict[str, str]]:
        return [
            {"server": server.text().strip(), "username": username.text(), "password": password.text()}
            for server, username, password in self.slots
        ]

    def apply_settings(self, settings: tuple[dict[str, str], ...]) -> None:
        for (server, username, password), slot in zip(self.slots, settings, strict=True):
            server.setText(slot["server"])
            username.setText(slot["username"])
            password.setText(slot["password"])

    def secrets(self) -> tuple[str, ...]:
        return tuple(value for _, username, password in self.slots for value in (
            username.text(), password.text(),
        ))
