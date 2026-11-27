"""Persistent browser profiles, recovery, and cleanup tests."""

from pathlib import Path
import tempfile
from unittest.mock import Mock

from playwright.sync_api import Error as PlaywrightError

from web_parser.browser_access import BrowserAccessError, BrowserPageAccess, browser_profile_dir
from web_parser.browser_discovery import BrowserInstallation
from web_parser.config import BROWSERS

from .base import BINARY, CHROME, URL, BrowserAccessTestCase


class BrowserProfileTests(BrowserAccessTestCase):
    def test_dedicated_persistent_profile_is_used_for_chromium(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "browser-profile"
            context = self.playwright.chromium.launch_persistent_context.return_value
            context.pages = [self.page]
            with BrowserPageAccess(CHROME, 2, profile_dir=profile) as access:
                result = access.fetch(URL)
            self.assertTrue(profile.is_dir())
        self.playwright.chromium.launch.assert_not_called()
        self.playwright.chromium.launch_persistent_context.assert_called_once_with(
            user_data_dir=str(profile), executable_path=str(BINARY),
            headless=False, locale="ru-RU",
            chromium_sandbox=True,
            timeout=20_000,
        )
        self.stealth.return_value.apply_stealth_sync.assert_called_once_with(context)
        context.new_page.assert_not_called()
        self.page.close.assert_called_once_with()
        context.close.assert_called_once_with()
        self.assertEqual(result.status, 200)

    def test_proxy_is_passed_to_persistent_chromium_context(self):
        with tempfile.TemporaryDirectory() as folder:
            context = self.playwright.chromium.launch_persistent_context.return_value
            context.pages = [self.page]
            proxy = {"server": "http://ru-proxy.example:8080"}
            with BrowserPageAccess(CHROME, 1, profile_dir=Path(folder), proxy=proxy):
                pass
        self.playwright.chromium.launch_persistent_context.assert_called_once_with(
            user_data_dir=folder, executable_path=str(BINARY),
            headless=False, locale="ru-RU", chromium_sandbox=True,
            timeout=20_000, proxy=proxy,
        )

    def test_proxy_is_passed_to_persistent_firefox_context(self):
        with tempfile.TemporaryDirectory() as folder:
            firefox = BrowserInstallation("Mozilla Firefox", BINARY)
            self.playwright.firefox.executable_path = str(BINARY)
            context = self.playwright.firefox.launch_persistent_context.return_value
            context.pages = [self.page]
            proxy = {"server": "socks5://ru-proxy.example:1080"}
            with BrowserPageAccess(firefox, 1, profile_dir=Path(folder), proxy=proxy):
                pass
        self.playwright.firefox.launch_persistent_context.assert_called_once_with(
            user_data_dir=folder, headless=False, locale="ru-RU", proxy=proxy,
        )

    def test_yandex_archives_saved_tabs_before_reopening_its_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "yandex"
            sessions = profile / "Default" / "Sessions"
            sessions.mkdir(parents=True)
            (sessions / "Session_1").write_bytes(b"stale tab data")
            cookies = profile / "Default" / "Network" / "Cookies"
            cookies.parent.mkdir()
            cookies.write_bytes(b"keep browser state")

            context = self.playwright.chromium.launch_persistent_context.return_value
            context.pages = [self.page]

            def check_profile(**_options):
                self.assertFalse(sessions.exists())
                self.assertEqual(cookies.read_bytes(), b"keep browser state")
                archives = list((profile / "WebParserSavedSessions").iterdir())
                self.assertEqual(len(archives), 1)
                self.assertEqual((archives[0] / "Session_1").read_bytes(), b"stale tab data")
                return context

            self.playwright.chromium.launch_persistent_context.side_effect = check_profile
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            with BrowserPageAccess(installation, 0, profile_dir=profile):
                pass

        self.playwright.chromium.launch_persistent_context.assert_called_once()

    def test_yandex_uses_temporary_profile_when_saved_profile_is_locked(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "yandex"
            context = self.playwright.chromium.launch_persistent_context.return_value
            context.pages = [self.page]
            self.playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError(
                    "Target page has been closed: GPUPersistentCache занят другим процессом (0x20)"
                ),
                context,
            ]
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            with BrowserPageAccess(installation, 0, profile_dir=profile) as access:
                self.assertIn("временным профилем", access.launch_notice or "")
                calls = self.playwright.chromium.launch_persistent_context.call_args_list
                fallback = Path(calls[1].kwargs["user_data_dir"])
                self.assertEqual(calls[0].kwargs["user_data_dir"], str(profile))
                self.assertNotEqual(fallback, profile)
                self.assertTrue(fallback.is_dir())
            self.assertFalse(fallback.exists())
            self.assertTrue(profile.is_dir())
        context.close.assert_called_once_with()

    def test_yandex_retries_target_closed_without_readable_lock_message(self):
        with tempfile.TemporaryDirectory() as folder:
            context = self.playwright.chromium.launch_persistent_context.return_value
            context.pages = [self.page]
            self.playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError(
                    "BrowserType.launch_persistent_context: "
                    "Target page, context or browser has been closed"
                ),
                context,
            ]
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            with BrowserPageAccess(installation, 0, profile_dir=Path(folder)) as access:
                self.assertIn("временным профилем", access.launch_notice or "")
        self.assertEqual(self.playwright.chromium.launch_persistent_context.call_count, 2)

    def test_yandex_does_not_retry_unrelated_launch_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            self.playwright.chromium.launch_persistent_context.side_effect = PlaywrightError(
                "Target page has been closed: incompatible browser"
            )
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            with self.assertRaisesRegex(BrowserAccessError, "incompatible browser"):
                with BrowserPageAccess(installation, 0, profile_dir=Path(folder)):
                    pass
        self.playwright.chromium.launch_persistent_context.assert_called_once()

    def test_yandex_uses_clean_session_if_temporary_profile_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            self.playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError("GPUPersistentCache занят другим процессом (0x20)"),
                PlaywrightError("temporary profile launch failed"),
            ]
            self.context.pages = []
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            with BrowserPageAccess(installation, 0, profile_dir=Path(folder)) as access:
                self.assertIn("чистом сеансе", access.launch_notice or "")
                fallback = Path(
                    self.playwright.chromium.launch_persistent_context.call_args_list[1]
                    .kwargs["user_data_dir"]
                )
            self.playwright.chromium.launch.assert_called_once_with(
                executable_path=str(BINARY), headless=False,
                chromium_sandbox=True, timeout=20_000,
            )
            self.assertFalse(fallback.exists())

    def test_yandex_reports_all_launch_failures_without_proxy_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            self.playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError("GPUPersistentCache занят другим процессом (0x20)"),
                PlaywrightError("temporary profile launch failed"),
            ]
            self.playwright.chromium.launch.side_effect = PlaywrightError(
                "BrowserType.launch: Target page, context or browser has been closed\n"
                "Browser logs:\n"
                "[pid=123][err] ERROR: proxy secret rejected\n"
                "[pid=123] <process did exit: exitCode=1, signal=null>"
            )
            installation = BrowserInstallation("Яндекс Браузер", BINARY)
            proxy = {
                "server": "http://proxy.example:8080", "username": "person", "password": "secret",
            }
            with self.assertRaisesRegex(BrowserAccessError, "чистый сеанс") as caught:
                with BrowserPageAccess(installation, 0, profile_dir=Path(folder), proxy=proxy):
                    pass
            fallback = Path(
                self.playwright.chromium.launch_persistent_context.call_args_list[1]
                .kwargs["user_data_dir"]
            )
            self.assertFalse(fallback.exists())
        self.assertIn("temporary profile launch failed", str(caught.exception))
        self.assertIn("stderr: ERROR: proxy [данные прокси скрыты] rejected", str(caught.exception))
        self.assertIn("exitCode=1", str(caught.exception))
        self.assertIn("[данные прокси скрыты]", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_repeated_persistent_runs_close_old_tabs_in_every_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in BROWSERS:
                with self.subTest(browser=name):
                    browser_type = (
                        self.playwright.firefox if name == "Mozilla Firefox"
                        else self.playwright.chromium
                    )
                    if name == "Mozilla Firefox":
                        browser_type.executable_path = str(BINARY)
                    first_context, second_context = Mock(), Mock()
                    first_page, next_page, old_page = Mock(), Mock(), Mock()
                    first_context.pages = [first_page]
                    second_context.pages = [next_page, old_page]
                    old_page.close.side_effect = lambda: second_context.pages.remove(old_page)
                    browser_type.launch_persistent_context.side_effect = [
                        first_context, second_context,
                    ]
                    profile = Path(folder) / name.replace(" ", "_")
                    installation = BrowserInstallation(name, BINARY)

                    with BrowserPageAccess(installation, 0, profile_dir=profile):
                        first_page.close.assert_not_called()
                    first_page.close.assert_called_once_with()
                    first_context.close.assert_called_once_with()

                    with BrowserPageAccess(installation, 0, profile_dir=profile):
                        old_page.close.assert_called_once_with()
                        next_page.close.assert_not_called()
                    next_page.close.assert_called_once_with()
                    second_context.close.assert_called_once_with()

    def test_profile_paths_are_separate_by_site_and_browser(self):
        first = browser_profile_dir("sail-master.ru", "Mozilla Firefox")
        second = browser_profile_dir("fishingline.ru", "Mozilla Firefox")
        third = browser_profile_dir("sail-master.ru", "Google Chrome")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(first.name, "firefox")
