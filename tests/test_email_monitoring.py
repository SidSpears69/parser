"""Safety stop and secure email notification integration."""

import json
import os
from pathlib import Path
import smtplib
import tempfile
import time
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from web_parser import config
from web_parser.browser_access import BrowserPageAccess, PageSnapshot
from web_parser.browser_discovery import BrowserInstallation
from web_parser.email_notifications import send_email
from web_parser.ozon import OzonLayoutError, parse_ozon_search
from web_parser.product_links import ProductLinks
from web_parser.ui.main_window import MainWindow
from web_parser.ui.workers import BrowserWalkThread
from web_parser.yandex_market import YandexMarketLayoutError, parse_yandex_search
from tests.test_config import sample_sites


EMAIL = {
    "enabled": True, "host": "smtp.example.org", "port": 465, "security": "ssl",
    "username": "parser@example.org", "password": "private-password",
    "sender": "parser@example.org", "recipient": "admin@example.org",
    "recipient2": "backup@example.org",
}


class EmailTests(unittest.TestCase):
    def test_ssl_sends_email_to_admin_and_hides_password_on_auth_error(self):
        class SMTP:
            def __init__(self):
                self.sent = None
                self.recipients = None
                self.credentials = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def login(self, username, password):
                self.credentials = (username, password)

            def send_message(self, letter, *, to_addrs):
                self.sent = letter
                self.recipients = to_addrs
                return {}

        client = SMTP()
        with patch("web_parser.email_notifications.smtplib.SMTP_SSL", return_value=client) as connect:
            send_email(EMAIL, "Ошибка парсера", critical=True)
        self.assertEqual(connect.call_args.args[:2], (EMAIL["host"], EMAIL["port"]))
        self.assertEqual(client.credentials, (EMAIL["username"], EMAIL["password"]))
        self.assertEqual(client.sent["To"], f'{EMAIL["recipient"]}, {EMAIL["recipient2"]}')
        self.assertEqual(client.recipients, [EMAIL["recipient"], EMAIL["recipient2"]])
        self.assertIn("аварийная остановка", client.sent["Subject"])
        self.assertIn("Ошибка парсера", client.sent.get_content())

        with patch("web_parser.email_notifications.smtplib.SMTP_SSL") as connect:
            connect.return_value.__enter__.return_value.login.side_effect = (
                smtplib.SMTPAuthenticationError(535, b"private-password")
            )
            with self.assertRaises(RuntimeError) as caught:
                send_email(EMAIL, "Ошибка парсера")
        self.assertIn("SMTP 535", str(caught.exception))
        self.assertNotIn(EMAIL["password"], str(caught.exception))

    def test_starttls_upgrades_before_authentication(self):
        with patch("web_parser.email_notifications.smtplib.SMTP") as connect:
            connect.return_value.__enter__.return_value.send_message.return_value = {}
            send_email({**EMAIL, "port": 587, "security": "starttls"}, "Тест")
        client = connect.return_value.__enter__.return_value
        client.starttls.assert_called_once()
        self.assertEqual(
            [call[0] for call in client.method_calls[:4]],
            ["ehlo", "starttls", "ehlo", "login"],
        )
        client.login.assert_called_once_with(EMAIL["username"], EMAIL["password"])
        client.send_message.assert_called_once()

    def test_reports_when_one_recipient_is_rejected(self):
        with patch("web_parser.email_notifications.smtplib.SMTP_SSL") as connect:
            connect.return_value.__enter__.return_value.send_message.return_value = {
                EMAIL["recipient2"]: (550, b"rejected"),
            }
            with self.assertRaisesRegex(RuntimeError, "один из адресов"):
                send_email(EMAIL, "Тест")

    def test_validates_addresses_and_secure_transport(self):
        self.assertEqual(config.validate_email(EMAIL), EMAIL)
        with self.assertRaises(config.ConfigError):
            config.validate_email({**EMAIL, "recipient": "admin@example.org\nBcc:other@example.org"})
        with self.assertRaises(config.ConfigError):
            config.validate_email({**EMAIL, "recipient2": "invalid-address"})
        with self.assertRaises(config.ConfigError):
            config.validate_email({**EMAIL, "recipient2": "ADMIN@example.org"})
        with self.assertRaises(config.ConfigError):
            config.validate_email({**EMAIL, "security": "plain"})
        self.assertEqual(config.validate_email({**EMAIL, "recipient2": ""})["recipient2"], "")
        self.assertEqual(config.validate_email({key: value for key, value in EMAIL.items() if key != "recipient2"})[
            "recipient2"
        ], "")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_config_round_trip_encrypts_smtp_password(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config.save(path, sample_sites(), email=EMAIL)
            self.assertNotIn(EMAIL["password"], path.read_text(encoding="utf-8"))
            self.assertEqual(config.load_document(path).email, EMAIL)
            previous_format = json.loads(path.read_text(encoding="utf-8"))
            previous_format["email"].pop("recipient2")
            path.write_text(json.dumps(previous_format, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(config.load_document(path).email["recipient2"], "")


class EmergencyStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_fatal_layout_stops_all_tabs_and_persists_until_manual_reset(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            window = MainWindow(log_root=root)
            try:
                first = window.pages[config.SITES[0]]
                first.schedule_enabled.setChecked(True)
                first.source_schedule_enabled.setChecked(True)
                first.fatal_layout.emit(
                    config.SITES[0], "1473", "Ozon", "https://www.ozon.ru/search/", "Нет карточек",
                )
                self.app.processEvents()
                self.assertTrue((root / "layout-halt.flag").exists())
                self.assertFalse(window.resume_button.isHidden())
                for page in window.pages.values():
                    self.assertFalse(page.schedule_enabled.isChecked())
                    self.assertFalse(page.source_schedule_enabled.isChecked())
                    self.assertFalse(page.start_button.isEnabled())
                    self.assertIsNone(page.scheduler.next_analysis)
                    self.assertIsNone(page.scheduler.next_source)
            finally:
                window.close()
            reopened = MainWindow(log_root=root)
            try:
                self.assertFalse(reopened.resume_button.isHidden())
                reopened.resume_button.click()
                self.assertFalse((root / "layout-halt.flag").exists())
                self.assertTrue(reopened.resume_button.isHidden())
                self.assertTrue(all(not p.schedule_enabled.isChecked() for p in reopened.pages.values()))
            finally:
                reopened.close()

    def test_layout_failure_aborts_remaining_pages_and_ftp(self):
        links = (ProductLinks(
            "1473", "https://www.ozon.ru/search/?text=1",
            "https://www.wildberries.ru/search/?text=1",
            "https://market.yandex.ru/search?text=1",
        ),)
        visited = []

        class Access:
            def __init__(self, *_args, **_kwargs):
                self.page = SimpleNamespace(request=object())
                self.playwright = object()
                self.effective_proxy = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def fetch(url, **_kwargs):
                visited.append(url)
                return PageSnapshot(url, url, 200, "<html>cards</html>", 1)

        class Wildberries:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        worker = BrowserWalkThread(
            config.SITES[0], BrowserInstallation("Google Chrome", Path(__file__)), links, 1,
            ftp_settings={"host": "ftp.example.org"},
        )
        failures = []
        worker.layout_failure.connect(lambda *args: failures.append(args))
        with (
            patch("web_parser.ui.workers.BrowserPageAccess", Access),
            patch("web_parser.ui.workers.WildberriesClient", Wildberries),
            patch("web_parser.ui.workers.parse_ozon_search", side_effect=OzonLayoutError("Нет карточек")),
            patch("web_parser.ui.workers.export_run") as export,
        ):
            worker.run()
        self.assertEqual(visited, [links[0].ozon_url])
        self.assertEqual(failures[0][1], "Ozon")
        self.assertEqual(worker.fatal_layout, "Нет карточек")
        export.assert_not_called()

    def test_failed_fallback_extraction_is_fatal_for_browser_marketplaces(self):
        class Page:
            def __init__(self, cards):
                self.cards = cards

            def wait_for_function(self, *_args, **_kwargs):
                pass

            def wait_for_selector(self, *_args, **_kwargs):
                pass

            def evaluate(self, *_args):
                return self.cards

        ozon_card = {
            "image_url": "https://ir.ozone.ru/s3/test.jpg",
            "product_url": "/product/test-1", "name": "Товар",
            "current_price": None, "_layout_fallbacks": ["цена"],
        }
        with self.assertRaises(OzonLayoutError):
            parse_ozon_search(cast(BrowserPageAccess, SimpleNamespace(page=Page([ozon_card]))))
        yandex_card = {
            "image_url": "https://avatars.mds.yandex.net/get-mpic/1/orig",
            "product_url": "/product--test/1", "name": "Товар",
            "price": None, "discounted_price": None, "_layout_fallbacks": ["цена"],
        }
        with self.assertRaises(YandexMarketLayoutError):
            parse_yandex_search(Mock(spec=BrowserPageAccess, page=Page([yandex_card])))

    def test_recorded_error_reaches_configured_admin_email(self):
        with tempfile.TemporaryDirectory() as folder:
            window = MainWindow(log_root=Path(folder))
            try:
                window.email_host.setText(str(EMAIL["host"]))
                window.email_username.setText(str(EMAIL["username"]))
                window.email_password.setText(str(EMAIL["password"]))
                window.email_sender.setText(str(EMAIL["sender"]))
                window.email_recipient.setText(str(EMAIL["recipient"]))
                window.email_recipient2.setText(str(EMAIL["recipient2"]))
                with patch("web_parser.email_notifications.send_email") as send:
                    window.email_enabled.setChecked(True)
                    window.pages[config.SITES[0]].log.append_error(
                        "1473", "https://www.ozon.ru/search/", "Ozon: ошибка теста",
                    )
                    deadline = time.monotonic() + 2
                    while not send.called and time.monotonic() < deadline:
                        QTest.qWait(20)
                    self.assertTrue(send.called)
                    self.assertEqual(send.call_args.args[0]["recipient"], EMAIL["recipient"])
                    self.assertEqual(send.call_args.args[0]["recipient2"], EMAIL["recipient2"])
                    self.assertIn("ошибка теста", send.call_args.args[1])
                    send.reset_mock()
                    window.email_test_button.click()
                    deadline = time.monotonic() + 2
                    while not send.called and time.monotonic() < deadline:
                        QTest.qWait(20)
                    self.assertTrue(send.called)
                    self.assertIn("тестовое письмо", send.call_args.args[1])
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
