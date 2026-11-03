"""Local image storage checks with a fake Playwright request context."""

from dataclasses import dataclass
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from playwright.sync_api import Error as PlaywrightError

from web_parser.images import ImageDownloadCancelled, ImageStore


OZON_IMAGE = "https://ir.ozone.ru/s3/photo.jpg"
WB_IMAGE = "https://basket-28.wbbasket.ru/vol1/part1/1/images/big/1.webp"
YANDEX_IMAGE = "https://avatars.mds.yandex.net/get-mpic/123/photo/orig"
JPEG = b"\xff\xd8\xff\xe0" + b"original JPEG bytes"
WEBP = b"RIFF\x10\x00\x00\x00WEBP" + b"original WEBP bytes"
AVIF = b"\x00\x00\x00\x18ftypavif" + b"original AVIF bytes"


@dataclass
class Product:
    image_url: str


def response(url: str, payload: bytes, *, status: int = 200, content_type: str = "image/jpeg") -> Mock:
    return Mock(
        url=url, ok=status == 200, status=status,
        headers={"content-type": content_type}, body=Mock(return_value=payload),
    )


class ImageStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.request = Mock()
        self.store = ImageStore(lambda: self.request, self.root, sleep=lambda _seconds: None)

    def test_saves_original_bytes_with_format_extension_and_stable_path(self):
        self.request.get.return_value = response(OZON_IMAGE, JPEG)
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/?text=rope",
        )
        self.assertEqual(batch.failed, ())
        self.assertEqual(len(batch.saved), 1)
        image = batch.saved[0]
        self.assertEqual(
            image.path,
            self.root / "sail-master.ru" / "images" / "1473" / "ozon" / "1.jpg",
        )
        self.assertEqual(image.path.read_bytes(), JPEG)
        self.assertEqual(image.size_bytes, len(JPEG))
        self.assertEqual(self.request.get.call_args.kwargs["timeout"], 20_000)
        self.assertEqual(
            self.request.get.call_args.kwargs["headers"]["Referer"],
            "https://www.ozon.ru/search/?text=rope",
        )
        self.request.get.return_value.dispose.assert_called_once_with()

    def test_referer_with_russian_search_text_is_ascii_encoded(self):
        self.request.get.return_value = response(OZON_IMAGE, JPEG)
        referer = "https://www.ozon.ru/search/?text=Шнур+яхтенный+Classic&from_global=true"
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Ozon", [Product(OZON_IMAGE)], referer,
        )
        self.assertEqual(batch.failed, ())
        self.assertEqual(
            self.request.get.call_args.kwargs["headers"]["Referer"],
            "https://www.ozon.ru/search/?text=%D0%A8%D0%BD%D1%83%D1%80+"
            "%D1%8F%D1%85%D1%82%D0%B5%D0%BD%D0%BD%D1%8B%D0%B9+Classic&from_global=true",
        )

    def test_same_module_saves_wildberries_and_yandex_images(self):
        for marketplace, url, payload, extension in (
            ("Wildberries", WB_IMAGE, WEBP, ".webp"),
            ("Яндекс Маркет", YANDEX_IMAGE, AVIF, ".avif"),
        ):
            with self.subTest(marketplace=marketplace):
                self.request.get.return_value = response(url, payload)
                batch = self.store.save_products(
                    "sail-master.ru", "1473", marketplace, [Product(url)],
                    "https://example.org/search",
                )
                self.assertEqual(batch.saved[0].path.suffix, extension)
                self.assertEqual(batch.saved[0].path.read_bytes(), payload)

    def test_bad_photo_does_not_discard_the_next_product(self):
        self.request.get.side_effect = [
            response(OZON_IMAGE, b"<html>challenge</html>", content_type="text/html"),
            response(OZON_IMAGE, JPEG),
        ]
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Ozon",
            [Product(OZON_IMAGE), Product(OZON_IMAGE)], "https://www.ozon.ru/search/",
        )
        self.assertEqual(len(batch.failed), 1)
        self.assertIn("другой тип данных", batch.failed[0].reason)
        self.assertEqual(batch.saved[0].position, 2)
        self.assertEqual(batch.saved[0].path.name, "2.jpg")

    def test_retries_temporary_http_error(self):
        self.request.get.side_effect = [
            response(OZON_IMAGE, b"", status=503), response(OZON_IMAGE, JPEG),
        ]
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/",
        )
        self.assertEqual(self.request.get.call_count, 2)
        self.assertEqual(len(batch.saved), 1)

    def test_retries_wildberries_photo_404_but_not_ozon_404(self):
        self.request.get.side_effect = [
            response(WB_IMAGE, b"", status=404),
            response(WB_IMAGE, WEBP, content_type="image/webp"),
        ]
        batch = self.store.save_products(
            "sail-master.ru", "1481", "Wildberries", [Product(WB_IMAGE)],
            "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
        )
        self.assertEqual(len(batch.saved), 1)
        self.assertEqual(self.request.get.call_count, 2)

        self.request.get.reset_mock()
        self.request.get.side_effect = None
        self.request.get.return_value = response(OZON_IMAGE, b"", status=404)
        batch = self.store.save_products(
            "sail-master.ru", "1481", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/?text=rope",
        )
        self.assertEqual(len(batch.failed), 1)
        self.assertEqual(self.request.get.call_count, 1)

    def test_retries_proxy_connection_errors_and_logs_safe_error_code(self):
        self.request.get.side_effect = [
            PlaywrightError("connect ECONNREFUSED 127.0.0.1:12345"),
            PlaywrightError("connect ECONNREFUSED 127.0.0.1:12345"),
            response(WB_IMAGE, WEBP, content_type="image/webp"),
        ]
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Wildberries", [Product(WB_IMAGE)],
            "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
        )
        self.assertEqual(len(batch.saved), 1)
        self.assertEqual(self.request.get.call_count, 3)

        self.request.get.reset_mock()
        self.request.get.side_effect = PlaywrightError(
            "connect ECONNREFUSED 127.0.0.1:12345; password=secret",
        )
        failed = self.store.save_products(
            "sail-master.ru", "1473", "Wildberries", [Product(WB_IMAGE)],
            "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
        )
        self.assertIn("ECONNREFUSED", failed.failed[0].reason)
        self.assertNotIn("secret", failed.failed[0].reason)

    def test_wildberries_images_retry_directly_after_proxy_404_or_connection_error(self):
        direct = Mock()
        direct.get.side_effect = [
            response(WB_IMAGE, WEBP, content_type="image/webp"),
            response(WB_IMAGE, WEBP, content_type="image/webp"),
        ]
        self.request.get.side_effect = [
            *[response(WB_IMAGE, b"", status=404) for _ in range(3)],
            *[PlaywrightError("SOCKS5 proxy rejected the connection") for _ in range(3)],
        ]
        store = ImageStore(
            lambda: self.request, self.root, sleep=lambda _seconds: None,
            fallback_request_provider=lambda: direct,
        )
        batch = store.save_products(
            "sail-master.ru", "1477", "Wildberries",
            [Product(WB_IMAGE), Product(WB_IMAGE)],
            "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
        )
        self.assertEqual(batch.failed, ())
        self.assertEqual(len(batch.saved), 2)
        self.assertEqual(self.request.get.call_count, 6)
        self.assertEqual(direct.get.call_count, 2)
        self.assertEqual(batch.saved[0].path.read_bytes(), WEBP)

    def test_ozon_image_retries_directly_after_proxy_timeout(self):
        direct = Mock()
        self.request.get.side_effect = PlaywrightError("Timeout 20000ms exceeded")
        direct.get.return_value = response(OZON_IMAGE, JPEG)
        store = ImageStore(
            lambda: self.request, self.root, sleep=lambda _seconds: None,
            fallback_request_provider=lambda: direct,
        )
        batch = store.save_products(
            "sail-master.ru", "2834", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/?text=anchor+cord+fl",
        )
        self.assertEqual(batch.failed, ())
        self.assertEqual(self.request.get.call_count, 3)
        direct.get.assert_called_once()
        self.assertEqual(batch.saved[0].path.read_bytes(), JPEG)

    def test_direct_ozon_image_retries_through_proxy_after_timeout(self):
        direct = Mock()
        direct.get.side_effect = PlaywrightError("Timeout 20000ms exceeded")
        self.request.get.return_value = response(OZON_IMAGE, JPEG)
        store = ImageStore(
            lambda: direct, self.root, sleep=lambda _seconds: None,
            fallback_request_provider=lambda: self.request,
            primary_route_label="Напрямую", fallback_route_label="через прокси",
        )
        batch = store.save_products(
            "sail-master.ru", "2834", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/?text=anchor+cord+fl",
        )
        self.assertEqual(batch.failed, ())
        self.assertEqual(direct.get.call_count, 3)
        self.request.get.assert_called_once()
        self.assertEqual(batch.saved[0].path.read_bytes(), JPEG)

    def test_wildberries_image_reports_both_routes_if_direct_request_also_fails(self):
        direct = Mock()
        self.request.get.return_value = response(WB_IMAGE, b"", status=404)
        direct.get.return_value = response(WB_IMAGE, b"", status=503)
        store = ImageStore(
            lambda: self.request, self.root, sleep=lambda _seconds: None,
            fallback_request_provider=lambda: direct,
        )
        batch = store.save_products(
            "sail-master.ru", "1477", "Wildberries", [Product(WB_IMAGE)],
            "https://www.wildberries.ru/catalog/0/search.aspx?search=rope",
        )
        self.assertEqual(len(batch.failed), 1)
        self.assertIn("Через прокси: Сервер фото вернул HTTP 404", batch.failed[0].reason)
        self.assertIn("напрямую: Сервер фото вернул HTTP 503", batch.failed[0].reason)

    def test_rejects_redirect_to_other_host_and_oversized_photo(self):
        self.request.get.return_value = response("https://other.example/photo.jpg", JPEG)
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/",
        )
        self.assertIn("за пределы CDN", batch.failed[0].reason)
        self.request.get.return_value = response(OZON_IMAGE, JPEG)
        self.request.get.return_value.headers["content-length"] = str(31 * 1024 * 1024)
        batch = self.store.save_products(
            "sail-master.ru", "1473", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/",
        )
        self.assertIn("30 МБ", batch.failed[0].reason)
        self.request.get.return_value.body.assert_not_called()

    def test_product_id_cannot_escape_site_folder(self):
        self.request.get.return_value = response(OZON_IMAGE, JPEG)
        batch = self.store.save_products(
            "sail-master.ru", "../../other", "Ozon", [Product(OZON_IMAGE)],
            "https://www.ozon.ru/search/",
        )
        self.assertTrue(batch.saved[0].path.is_relative_to(self.root / "sail-master.ru"))
        self.assertNotIn("..", batch.saved[0].path.parts)

    def test_stop_request_aborts_before_network_or_disk(self):
        store = ImageStore(lambda: self.request, self.root, cancel_requested=lambda: True)
        with self.assertRaises(ImageDownloadCancelled):
            store.save_products(
                "sail-master.ru", "1473", "Ozon", [Product(OZON_IMAGE)],
                "https://www.ozon.ru/search/",
            )
        self.request.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
