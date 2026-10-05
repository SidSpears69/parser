"""YandexDriver selection and cache checks without external network access."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from web_parser import yandex_driver


def archive_with_driver(path: str = "driver/yandexdriver.exe", content: bytes = b"MZ-test-driver") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr(path, content)
    return output.getvalue()


class YandexDriverTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix=".test-yandex-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name)

    @staticmethod
    def releases(archive: bytes, *, digest: str | None = None) -> bytes:
        return json.dumps([
            {"tag_name": "v26.8.1-stable", "assets": [
                {"name": "yandexdriver-26.8.1.1019-linux.zip"},
            ]},
            {"tag_name": "v26.8.0-stable", "assets": [
                {"name": "yandexdriver-26.8.0.1788-win64.zip", "size": len(archive),
                 "browser_download_url": "https://github.com/yandex/YandexDriver/releases/download/"
                                         "v26.8.0-stable/yandexdriver-26.8.0.1788-win64.zip",
                 "digest": digest},
            ]},
        ]).encode("utf-8")

    def test_selects_compatible_windows_asset_and_reuses_verified_cache(self):
        archive = archive_with_driver()
        digest = "sha256:" + hashlib.sha256(archive).hexdigest()
        releases = self.releases(archive, digest=digest)

        def response(url, *_args, **_kwargs):
            return releases if "api.github.com" in url else archive

        with mock.patch.object(yandex_driver, "_request", side_effect=response) as request:
            exe = yandex_driver.ensure_yandex_driver("26.8.1.1019", self.cache)
            self.assertEqual(exe.read_bytes(), b"MZ-test-driver")
            self.assertEqual(yandex_driver.ensure_yandex_driver("26.8.2.1", self.cache), exe)
            self.assertEqual(request.call_count, 2)

    def test_bad_archive_digest_is_rejected_without_cache_entry(self):
        archive = archive_with_driver()
        releases = self.releases(archive, digest="sha256:" + "0" * 64)
        with mock.patch.object(yandex_driver, "_request", side_effect=[releases, archive]):
            with self.assertRaisesRegex(yandex_driver.YandexDriverError, "Контрольная сумма"):
                yandex_driver.ensure_yandex_driver("26.8.1", self.cache)
        self.assertFalse((self.cache / "26.8" / "yandexdriver.exe").exists())

    def test_unsafe_zip_path_is_rejected(self):
        archive = archive_with_driver("../yandexdriver.exe")
        releases = self.releases(archive)
        with mock.patch.object(yandex_driver, "_request", side_effect=[releases, archive]):
            with self.assertRaisesRegex(yandex_driver.YandexDriverError, "небезопасный путь"):
                yandex_driver.ensure_yandex_driver("26.8.1", self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_no_matching_windows_release_explains_problem(self):
        releases = json.dumps([{"tag_name": "v26.8.1-stable", "assets": [
            {"name": "yandexdriver-26.8.1.1019-linux.zip"},
        ]}]).encode("utf-8")
        with mock.patch.object(yandex_driver, "_request", return_value=releases):
            with self.assertRaisesRegex(yandex_driver.YandexDriverError, "Windows x64"):
                yandex_driver.ensure_yandex_driver("26.8.1", self.cache)

    def test_corrupted_cache_is_downloaded_again(self):
        archive = archive_with_driver()
        releases = self.releases(archive)

        def response(url, *_args, **_kwargs):
            return releases if "api.github.com" in url else archive

        with mock.patch.object(yandex_driver, "_request", side_effect=response):
            exe = yandex_driver.ensure_yandex_driver("26.8.1", self.cache)
            exe.write_bytes(b"damaged")
            repaired = yandex_driver.ensure_yandex_driver("26.8.1", self.cache)
        self.assertEqual(repaired.read_bytes(), b"MZ-test-driver")


if __name__ == "__main__":
    unittest.main()
