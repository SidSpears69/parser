"""FTP export handoff and status on a site tab."""

from unittest.mock import patch

from web_parser.browser_access import BrowserFetchFailed, PageSnapshot
from web_parser.ftp_export import FTPExportError, FTPExportResult
from web_parser.images import ImageBatch, ImageFailure
from web_parser.product_links import ProductLinks
from web_parser.ui import SITES
from web_parser.wildberries import WildberriesError

from .base import UiTestCase


class FTPPageTests(UiTestCase):
    def setUp(self):
        super().setUp()
        self.page = self.window.pages[SITES[0]]
        self._load_links(self.page, [ProductLinks(
            "1473", "https://ozon.example/search", "https://wb.example/search",
            "https://market.example/search",
        )])
        self.page.ftp_host.setText("ftp.example.org")
        self.page.ftp_user.setText("publisher")
        self.page.ftp_password.setText("secret-ftp-password")
        self.page.remote_path.setText("/exports")

    def _walk(self, exporter=None, *, failed_urls: tuple[str, ...] = ()):
        class FakeAccess:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            @staticmethod
            def fetch(url, **_kwargs):
                if url in failed_urls:
                    raise BrowserFetchFailed("Не удалось загрузить страницу.")
                return PageSnapshot(url, url, 200, "<html>Товар</html>", 1)

            @staticmethod
            def fetch_wildberries_json(url):
                if url in failed_urls:
                    raise BrowserFetchFailed("Не удалось загрузить страницу Wildberries.")
                return {"products": []}

        with (
            patch("web_parser.ui.workers.BrowserPageAccess", FakeAccess),
            patch("web_parser.ui.workers.export_run", side_effect=exporter) as upload,
        ):
            self.page.start_button.click()
            self._wait_for_walk(self.page)
        return upload

    def _assert_ftp_skipped(self, upload) -> None:
        upload.assert_not_called()
        for text in (self.page.ftp_status.text(), self.page.log.editor.toPlainText()):
            self.assertIn("Выгрузка пропущена", text)
            self.assertIn("Предыдущие данные на сервере сохранены", text)

    def test_successful_upload_receives_current_results_and_is_shown(self):
        upload = self._walk(lambda *_args, **_kwargs: FTPExportResult(
            "/exports/processed_products.json", 2, 1,
        ))
        self.assertEqual(upload.call_count, 1)
        args = upload.call_args.args
        self.assertEqual(args[0], SITES[0])
        self.assertEqual(args[2]["Ozon"]["1473"], self.page.ozon_results["1473"])
        self.assertEqual(args[3], self.page.saved_images)
        self.assertEqual(args[4]["password"], "secret-ftp-password")
        self.assertIn("2 фото и JSON выгружены", self.page.run_status.text())
        self.assertIn(
            "/exports/processed_products.json",
            self.page.ftp_status.text().replace("\u200b", ""),
        )

    def test_failed_upload_is_logged_without_exposing_password(self):
        self._walk(lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FTPExportError("FTP отклонил запись файла."),
        ))
        self.assertIn("FTP: ошибка выгрузки", self.page.run_status.text())
        self.assertIn("FTP отклонил запись файла", self.page.log.editor.toPlainText())
        self.assertNotIn("secret-ftp-password", self.page.log.editor.toPlainText())

    def test_all_pages_unavailable_keeps_previous_publication(self):
        links = self.page.product_links[0]
        with patch(
            "web_parser.ui.workers.WildberriesClient.fetch",
            side_effect=WildberriesError("Не удалось получить данные Wildberries."),
        ):
            upload = self._walk(failed_urls=(links.ozon_url, links.wildberries_url, links.yandex_market_url))
        self.assertIn("Загружено: 0; ошибок: 3", self.page.run_status.text())
        self._assert_ftp_skipped(upload)

    def test_partial_results_keep_previous_publication(self):
        upload = self._walk(failed_urls=(self.page.product_links[0].ozon_url,))
        self.assertIn("Загружено: 2; ошибок: 1", self.page.run_status.text())
        self.assertTrue(self.page.wildberries_results["1473"])
        self._assert_ftp_skipped(upload)

    def test_missing_photo_keeps_previous_publication_after_successful_pages(self):
        incomplete = ImageBatch((), (ImageFailure(1, "https://images.example/product.jpg", "Сбой загрузки"),))
        with patch(
            "web_parser.ui.workers.ImageStore.save_products",
            side_effect=(incomplete, ImageBatch((), ()), ImageBatch((), ())),
        ):
            upload = self._walk()
        self.assertIn("Загружено: 3; ошибок: 0", self.page.run_status.text())
        self.assertIn("ошибок фото: 1", self.page.run_status.text())
        self._assert_ftp_skipped(upload)
