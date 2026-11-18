"""Background delivery of parser warnings and errors by secure SMTP."""

from __future__ import annotations

from email.message import EmailMessage
from queue import Empty, Full, Queue
import smtplib
import ssl
from threading import Thread
from time import sleep

from PySide6.QtCore import QObject, Signal

from web_parser.config import default_email, validate_email


EmailSettings = dict[str, str | int | bool]


def send_email(settings: EmailSettings, body: str, *, critical: bool = False) -> None:
    """Send one message without putting SMTP credentials or server replies in errors."""
    checked = validate_email(settings)
    if not checked["enabled"]:
        return
    try:
        recipients = [str(checked["recipient"])]
        if checked["recipient2"]:
            recipients.append(str(checked["recipient2"]))
        letter = EmailMessage()
        letter["From"] = str(checked["sender"])
        letter["To"] = ", ".join(recipients)
        letter["Subject"] = (
            "Web Parser: аварийная остановка" if critical else "Web Parser: уведомление"
        )
        letter.set_content(body)
        context = ssl.create_default_context()
        host = str(checked["host"])
        port = int(checked["port"])
        if checked["security"] == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=15)
        with client as smtp:
            if checked["security"] == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if checked["username"]:
                smtp.login(str(checked["username"]), str(checked["password"]))
            if smtp.send_message(letter, to_addrs=recipients):
                raise RuntimeError("SMTP отклонил один из адресов получателей")
    except smtplib.SMTPAuthenticationError as error:
        raise RuntimeError(f"SMTP {error.smtp_code}: ошибка входа") from None
    except smtplib.SMTPRecipientsRefused:
        raise RuntimeError("SMTP отклонил адрес получателя") from None
    except smtplib.SMTPResponseException as error:
        raise RuntimeError(f"SMTP {error.smtp_code}: сервер отклонил команду") from None
    except (smtplib.SMTPException, OSError, TimeoutError):
        raise RuntimeError("SMTP: ошибка соединения или тайм-аут") from None
    except (UnicodeError, ValueError):
        raise RuntimeError("Не удалось подготовить письмо для отправки") from None


class EmailNotifier(QObject):
    sent = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = validate_email(default_email())
        self._queue: Queue[tuple[EmailSettings, str]] = Queue(maxsize=100)
        self._critical: Queue[tuple[EmailSettings, str] | None] = Queue(maxsize=10)
        self._thread = Thread(target=self._run, name="email-notifications", daemon=True)
        self._thread.start()

    def configure(self, settings: EmailSettings) -> None:
        self._settings = validate_email(settings)

    def submit(self, message: str, *, critical: bool = False) -> None:
        if not self._settings["enabled"]:
            return
        try:
            target = self._critical if critical else self._queue
            target.put_nowait((self._settings.copy(), message[:3500]))
        except Full:
            self.failed.emit("Очередь почтовых уведомлений переполнена.")

    def close(self) -> None:
        try:
            self._critical.put_nowait(None)
        except Full:
            pass

    def _run(self) -> None:
        while True:
            try:
                item = self._critical.get_nowait()
                critical = True
            except Empty:
                try:
                    item = self._queue.get(timeout=0.5)
                    critical = False
                except Empty:
                    continue
            if item is None:
                return
            try:
                for attempt in range(2 if critical else 1):
                    try:
                        send_email(*item, critical=critical)
                    except RuntimeError:
                        if attempt == 0 and critical:
                            sleep(1)
                            continue
                        raise
                    else:
                        break
            except RuntimeError as error:
                self.failed.emit(str(error))
            else:
                self.sent.emit()
            finally:
                (self._critical if critical else self._queue).task_done()
