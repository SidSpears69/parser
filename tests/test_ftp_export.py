"""FTP export layout and publication checks without an external server."""

from datetime import datetime, timezone
from decimal import Decimal
from ftplib import error_perm
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from web_parser.ftp_export import FTPExportCancelled, FTPExportError, build_document, export_run
from web_parser.images import SavedImage
from web_parser.ozon import OzonProduct
from web_parser.product_links import ProductLinks
from web_parser.wildberries import WildberriesProduct


SITE = "sail-master.ru"
SETTINGS = {
    "host": "ftp.example.org", "port": 2121,
    "username": "user", "password": "secret", "remote_path": "/exports",
}


class MemoryFTP:
    def __init__(self, *, home="/"):
        self.directories = {"/", home}
        self.files: dict[str, bytes] = {}
        self.current = home
        self.commands: list[str] = []
        self.connection: tuple[str, int, int] | None = None
        self.credentials: tuple[str, str] | None = None
        self.passive: bool | None = None
        self.fail_image = False
        self.closed = False

    def connect(self, host, port, *, timeout):
        self.connection = (host, port, timeout)

    def login(self, username, password):
        self.credentials = (username, password)

    def set_pasv(self, value):
        self.passive = value

    def pwd(self):
        return self.current

    def _path(self, name):
        if name.startswith("/"):
            return name
        return f"{self.current.rstrip('/')}/{name}"

    def cwd(self, name):
        path = self._path(name)
        if path not in self.directories:
            raise error_perm("550 missing directory")
        self.current = path

    def mkd(self, name):
        self.directories.add(self._path(name))

    def storbinary(self, command, source, *, callback=None):
        name = command.removeprefix("STOR ")
        path = self._path(name)
        self.commands.append(f"STOR {path}")
        if self.fail_image and "/images/" in path:
            raise error_perm("550 forbidden")
        payload = source.read()
        if callback is not None:
            callback(payload)
        self.files[path] = payload

    def rename(self, source, target):
        old = self._path(source)
        new = self._path(target)
        self.commands.append(f"RENAME {old} {new}")
        if new in self.files:
            raise error_perm("550 exists")
        self.files[new] = self.files.pop(old)

    def delete(self, name):
        path = self._path(name)
        if path not in self.files:
            raise error_perm("550 missing file")
        del self.files[path]

    def close(self):
        self.closed = True


class FTPExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.ozon = OzonProduct(
            "https://ir.ozone.ru/photo.jpg", Decimal("1000.50"), Decimal("750"),
            "Шнур", Decimal("4.8"), 12, "https://www.ozon.ru/product/shnur-123/",
        )
        self.wildberries = WildberriesProduct(
            12345, "https://basket-01.wbbasket.ru/photo.webp",
            Decimal("900"), Decimal("800"), "Шнур WB", None, 0,
            "https://www.wildberries.ru/catalog/12345/detail.aspx",
        )
        self.links = (ProductLinks(
            "1473", "https://ozon.example/search", "https://wb.example/search",
            "https://market.example/search",
        ),)
        path = self.root / SITE / "images" / "1473" / "ozon" / "1.jpg"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xd8\xfforiginal photo bytes")
        self.images = {("1473", "Ozon"): (
            SavedImage("1473", "Ozon", 1, self.ozon.image_url, path, path.stat().st_size),
        )}
        self.results = {
            "Ozon": {"1473": (self.ozon,)},
            "Wildberries": {"1473": (self.wildberries,)},
            "Яндекс Маркет": {},
        }

    def test_json_contains_six_fields_and_only_saved_photo_paths(self):
        document = build_document(
            SITE, self.links, self.results, self.images, image_root=self.root,
            generated_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(document["site"], SITE)
        self.assertEqual(document["generated_at"], "2026-11-03T00:00:00+00:00")
        marketplaces = document["products"][0]["marketplaces"]
        ozon = marketplaces["ozon"]["products"][0]
        self.assertEqual(ozon["price"], "1000.50")
        self.assertEqual(ozon["image_path"], "images/1473/ozon/1.jpg")
        self.assertEqual(ozon["reviews"], 12)
        self.assertEqual(ozon["product_url"], "https://www.ozon.ru/product/shnur-123/")
        wb = marketplaces["wildberries"]["products"][0]
        self.assertEqual(wb["wb_id"], 12345)
        self.assertEqual(wb["product_url"], "https://www.wildberries.ru/catalog/12345/detail.aspx")
        self.assertIsNone(wb["rating"])
        self.assertIsNone(wb["image_path"])
        self.assertEqual(marketplaces["yandex_market"]["products"], [])
        self.assertEqual(marketplaces["yandex_market"]["status"], "error")
        self.assertEqual(marketplaces["wildberries"]["status"], "ok")

    def test_uploads_original_image_before_publishing_json_in_base_directory(self):
        fake = MemoryFTP()
        with patch("web_parser.ftp_export.FTP", return_value=fake):
            result = export_run(
                SITE, self.links, self.results, self.images, SETTINGS, image_root=self.root,
            )
        self.assertEqual(fake.connection, ("ftp.example.org", 2121, 20))
        self.assertEqual(fake.credentials, ("user", "secret"))
        self.assertTrue(fake.passive)
        self.assertTrue(fake.closed)
        output = "/exports/processed_products.json"
        document = json.loads(fake.files[output])
        product = document["products"][0]["marketplaces"]["ozon"]["products"][0]
        self.assertEqual(product["product_url"], "https://www.ozon.ru/product/shnur-123/")
        relative = product["image_path"]
        photo = f"/exports/{relative}"
        self.assertEqual(fake.files[photo], self.images[("1473", "Ozon")][0].path.read_bytes())
        self.assertIn(output, fake.files)
        self.assertLess(
            fake.commands.index(f"STOR {photo}"),
            next(index for index, command in enumerate(fake.commands)
                 if command.startswith("STOR /exports/.processed_products.json.uploading-")),
        )
        self.assertEqual(result.remote_json, output)
        self.assertEqual(result.uploaded_images, 1)
        self.assertEqual(result.products, 1)

    def test_second_export_keeps_previous_photos_and_replaces_json(self):
        fake = MemoryFTP()
        with patch("web_parser.ftp_export.FTP", return_value=fake):
            export_run(SITE, self.links, self.results, self.images, SETTINGS, image_root=self.root)
            output = "/exports/processed_products.json"
            first = json.loads(fake.files[output])
            first_relative = first["products"][0]["marketplaces"]["ozon"]["products"][0]["image_path"]
            first_photo = f"/exports/{first_relative}"
            original_bytes = fake.files[first_photo]
            self.images[("1473", "Ozon")][0].path.write_bytes(b"\xff\xd8\xffnew photo")
            fake.current = "/"
            export_run(SITE, self.links, self.results, self.images, SETTINGS, image_root=self.root)
        second = json.loads(fake.files[output])
        second_relative = second["products"][0]["marketplaces"]["ozon"]["products"][0]["image_path"]
        self.assertNotEqual(first_relative, second_relative)
        self.assertEqual(fake.files[first_photo], original_bytes)
        self.assertEqual(fake.files[f"/exports/{second_relative}"], b"\xff\xd8\xffnew photo")
        self.assertFalse(any(".processed_products.json.backup-" in path for path in fake.files))

    def test_failed_photo_does_not_publish_json(self):
        fake = MemoryFTP()
        fake.fail_image = True
        with patch("web_parser.ftp_export.FTP", return_value=fake):
            with self.assertRaises(FTPExportError):
                export_run(SITE, self.links, self.results, self.images, SETTINGS, image_root=self.root)
        self.assertNotIn("/exports/processed_products.json", fake.files)
        self.assertTrue(fake.closed)

    def test_slash_uses_ftp_root_as_base_directory(self):
        fake = MemoryFTP(home="/account-home")
        with patch("web_parser.ftp_export.FTP", return_value=fake):
            result = export_run(
                SITE, self.links, self.results, self.images,
                {**SETTINGS, "remote_path": "/"}, image_root=self.root,
            )
        self.assertEqual(result.remote_json, "/account-home/processed_products.json")
        self.assertIn("/account-home/processed_products.json", fake.files)
        self.assertTrue(any(path.startswith("/account-home/images/") for path in fake.files))

    def test_cancellation_during_photo_transfer_does_not_publish_json(self):
        fake = MemoryFTP()
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 2

        with patch("web_parser.ftp_export.FTP", return_value=fake):
            with self.assertRaises(FTPExportCancelled):
                export_run(
                    SITE, self.links, self.results, self.images, SETTINGS,
                    image_root=self.root, cancel_requested=cancelled,
                )
        self.assertNotIn("/exports/processed_products.json", fake.files)

    def test_rejects_path_traversal_before_connecting(self):
        with patch("web_parser.ftp_export.FTP") as constructor:
            with self.assertRaises(FTPExportError):
                export_run(
                    SITE, self.links, self.results, self.images,
                    {**SETTINGS, "remote_path": "/exports/../other"}, image_root=self.root,
                )
        constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
