"""Checks for Windows browser discovery without launching a browser."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from web_parser import browser_discovery, config


class BrowserDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("browser-discovery-test")
        self.files: set[Path] = set()

    def executable(self, relative: str) -> Path:
        path = self.root / relative
        self.files.add(path)
        return path

    def test_finds_all_four_and_prefers_registry_then_standard_folders_then_path(self):
        chrome_registry = self.executable("registry/chrome.exe")
        self.executable("local/Google/Chrome/Application/chrome.exe")
        yandex_local = self.executable("local/Yandex/YandexBrowser/Application/browser.exe")
        edge_program = self.executable("program/Microsoft/Edge/Application/msedge.exe")
        firefox_path = self.executable("bin/firefox.exe")

        def registry_paths(executable):
            if executable == "chrome.exe":
                return iter((chrome_registry,))
            if executable == "msedge.exe":
                return iter((self.root / "stale/msedge.exe",))
            return iter(())

        environment = {
            "LOCALAPPDATA": str(self.root / "local"),
            "PROGRAMFILES": str(self.root / "program"),
            "PATH": str(self.root / "bin"),
        }
        with mock.patch.object(browser_discovery, "_windows_host", return_value=True), \
                mock.patch.object(browser_discovery, "_registry_paths", side_effect=registry_paths), \
                mock.patch.object(Path, "is_file", autospec=True,
                                  side_effect=lambda path: path in self.files), \
                mock.patch.dict(os.environ, environment, clear=True):
            found = browser_discovery.discover_browsers()

        self.assertEqual(tuple(found), config.BROWSERS)
        self.assertEqual(found[config.BROWSERS[0]].path, yandex_local)
        self.assertEqual(found[config.BROWSERS[1]].path, chrome_registry)
        self.assertEqual(found[config.BROWSERS[2]].path, edge_program)
        self.assertEqual(found[config.BROWSERS[3]].path, firefox_path)
        with self.assertRaises(FrozenInstanceError):
            found[config.BROWSERS[1]].name = "Other"

    def test_registry_reads_both_views_and_ignores_missing_keys(self):
        installed = self.executable("from_registry/firefox.exe")
        calls = []

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def open_key(root, name, reserved, access):
            calls.append((root, name, reserved, access))
            if root == 20 and access == 1 | 4:
                return FakeKey()
            raise FileNotFoundError

        fake_winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER=10, HKEY_LOCAL_MACHINE=20,
            KEY_READ=1, KEY_WOW64_64KEY=2, KEY_WOW64_32KEY=4,
            OpenKey=open_key, QueryValueEx=lambda key, name: (f'"{installed}"', 1),
        )
        with mock.patch.dict(sys.modules, {"winreg": fake_winreg}):
            paths = list(browser_discovery._registry_paths("firefox.exe"))

        self.assertEqual(paths, [installed])
        self.assertEqual([(root, access) for root, _, _, access in calls],
                         [(10, 3), (10, 5), (20, 3), (20, 5)])
        self.assertTrue(all(name.endswith(r"App Paths\firefox.exe") for _, name, _, _ in calls))

    def test_non_windows_returns_empty_without_probing(self):
        with mock.patch.object(browser_discovery, "_windows_host", return_value=False), \
                mock.patch.object(browser_discovery, "_registry_paths", side_effect=AssertionError("registry used")):
            self.assertEqual(browser_discovery.discover_browsers(), {})


if __name__ == "__main__":
    unittest.main()
