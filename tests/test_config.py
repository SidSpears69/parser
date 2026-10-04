"""Configuration file checks: python -m unittest tests.test_config -v."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from web_parser import config


def sample_sites() -> dict[str, dict]:
    return {
        site: {
            "site": site,
            "source_url": f"https://{site}/products.json",
            "token": f"secret-token-{index}-пароль",
            "browser": config.BROWSERS[index],
            "pause_seconds": index + 1,
            "ftp": {
                "host": f"ftp.{site}", "port": 2100 + index,
                "username": f"user-{index}", "password": f"secret-ftp-{index}-пароль",
                "remote_path": f"/site-{index}/",
            },
            "schedule": {
                "enabled": index % 2 == 0, "start_time": f"0{index}:30",
                "interval": index + 1, "unit": config.INTERVAL_UNITS[index],
            },
        }
        for index, site in enumerate(config.SITES)
    }


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "config.json"

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_round_trip_encrypts_each_sites_secrets(self):
        original = sample_sites()
        config.save(self.path, original)
        saved = self.path.read_text(encoding="utf-8")
        self.assertEqual(config.load(self.path), original)
        self.assertEqual(json.loads(saved)["version"], config.VERSION)
        for settings in original.values():
            self.assertNotIn(settings["token"], saved)
            self.assertNotIn(settings["ftp"]["password"], saved)
        self.assertEqual(original, sample_sites(), "Export must not change the caller's data")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_corrupt_later_site_rejects_complete_import(self):
        config.save(self.path, sample_sites())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        last = config.SITES[-1]
        saved["sites"][last]["ftp"]["password"] = {"dpapi": "not base64!"}
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load(self.path)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_plaintext_secret_is_rejected(self):
        config.save(self.path, sample_sites())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        saved["sites"][config.SITES[0]]["token"] = "plaintext"
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load(self.path)

    def test_invalid_settings_leave_existing_file_unchanged(self):
        self.path.write_text("existing configuration", encoding="utf-8")
        sites = sample_sites()
        sites[config.SITES[0]]["ftp"]["port"] = True
        with self.assertRaises(config.ConfigError):
            config.save(self.path, sites)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "existing configuration")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_failed_replace_preserves_old_file_and_cleans_temp(self):
        self.path.write_text("existing configuration", encoding="utf-8")
        with mock.patch("web_parser.config.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(config.ConfigError):
                config.save(self.path, sample_sites())
        self.assertEqual(self.path.read_text(encoding="utf-8"), "existing configuration")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_file_version_rejected(self):
        self.path.write_text('{"version": 999, "sites": {}}', encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.load(self.path)


if __name__ == "__main__":
    unittest.main()
