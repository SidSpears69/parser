"""Download and save marketplace primary photos without modifying their bytes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Protocol
from urllib.parse import quote, urlsplit

from playwright.sync_api import APIRequestContext, Error as PlaywrightError

from web_parser.config import SITES
from web_parser.network_errors import request_failure_reason


DEFAULT_IMAGE_ROOT = Path(__file__).resolve().parent.parent / "artifacts"
_MAX_IMAGE_BYTES = 30 * 1024 * 1024
_REQUEST_TIMEOUT_MS = 20_000
_ATTEMPTS = 3
_REFERER_SAFE = ":/?#[]@!$&'()*+,;=%-._~"
_MARKETPLACES = {
    "Ozon": "ozon",
    "Wildberries": "wildberries",
    "Яндекс Маркет": "yandex_market",
}
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
    "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


class ImageDownloadError(RuntimeError):
    """A marketplace photo could not be downloaded or saved."""


class ImageDownloadCancelled(ImageDownloadError):
    """The user stopped the browser walk during photo processing."""


class ProductWithImage(Protocol):
    image_url: str


@dataclass(frozen=True, slots=True)
class SavedImage:
    product_id: str
    marketplace: str
    position: int
    source_url: str
    path: Path
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ImageFailure:
    position: int
    source_url: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImageBatch:
    saved: tuple[SavedImage, ...]
    failed: tuple[ImageFailure, ...]


def _safe_product_id(product_id: str) -> str:
    if (re.fullmatch(r"[A-Za-z0-9_-]{1,64}", product_id)
            and product_id.upper() not in _WINDOWS_RESERVED):
        return product_id
    return sha256(product_id.encode("utf-8")).hexdigest()[:24]


def _check_image_url(url: str, marketplace: str) -> None:
    if not isinstance(url, str):
        raise ImageDownloadError("Некорректная ссылка на фото.")
    try:
        parts = urlsplit(url)
        valid = parts.scheme == "https" and not parts.username and not parts.password
        host = parts.hostname or ""
        _ = parts.port
    except ValueError:
        valid, host = False, ""
    valid_host = (
        host == "ir.ozone.ru" if marketplace == "Ozon" else
        host.endswith(".wbbasket.ru") if marketplace == "Wildberries" else
        host == "avatars.mds.yandex.net"
    )
    if not valid or not valid_host:
        raise ImageDownloadError("Фото ведёт за пределы CDN маркетплейса.")


def _image_extension(payload: bytes) -> str:
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp"
    if payload[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if payload[4:8] == b"ftyp" and b"avif" in payload[8:32]:
        return ".avif"
    raise ImageDownloadError("Сервер вернул файл без распознаваемого формата изображения.")


class ImageStore:
    """Save primary photos for all marketplaces using the active browser request context."""

    def __init__(
        self, request_provider: Callable[[], APIRequestContext],
        root: Path = DEFAULT_IMAGE_ROOT, *,
        cancel_requested: Callable[[], bool] = lambda: False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._request_provider = request_provider
        self.root = Path(root)
        self._cancel_requested = cancel_requested
        self._sleep = sleep

    def _check_cancelled(self) -> None:
        if self._cancel_requested():
            raise ImageDownloadCancelled("Загрузка фотографий остановлена.")

    def _download(self, url: str, marketplace: str, referer: str) -> tuple[bytes, str]:
        _check_image_url(url, marketplace)
        last_problem = "Не удалось загрузить фото."
        for attempt in range(_ATTEMPTS):
            self._check_cancelled()
            response = None
            try:
                response = self._request_provider().get(
                    url, headers={"Accept": "image/*", "Referer": quote(referer, safe=_REFERER_SAFE)},
                    timeout=_REQUEST_TIMEOUT_MS, max_redirects=3,
                )
                _check_image_url(response.url, marketplace)
                if not response.ok:
                    last_problem = f"Сервер фото вернул HTTP {response.status}."
                    retryable = (
                        response.status in (408, 429)
                        or response.status >= 500
                        or (marketplace == "Wildberries" and response.status == 404)
                    )
                    if not retryable:
                        break
                else:
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type and not (
                        content_type.startswith("image/") or content_type == "application/octet-stream"
                    ):
                        raise ImageDownloadError("Вместо фото сервер вернул другой тип данных.")
                    length = response.headers.get("content-length", "")
                    if length.isdecimal() and int(length) > _MAX_IMAGE_BYTES:
                        raise ImageDownloadError("Фото превышает допустимый размер 30 МБ.")
                    payload = response.body()
                    if len(payload) > _MAX_IMAGE_BYTES:
                        raise ImageDownloadError("Фото превышает допустимый размер 30 МБ.")
                    return payload, _image_extension(payload)
            except PlaywrightError as error:
                last_problem = f"Не удалось загрузить фото по сети: {request_failure_reason(error)}."
            finally:
                if response is not None:
                    try:
                        response.dispose()
                    except PlaywrightError:
                        pass
            if attempt + 1 < _ATTEMPTS:
                self._sleep(1)
        raise ImageDownloadError(last_problem)

    @staticmethod
    def _save(path: Path, payload: bytes) -> None:
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=".image-", delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise ImageDownloadError("Не удалось сохранить фото на диск.") from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def save_products(
        self, site: str, product_id: str, marketplace: str,
        products: Sequence[ProductWithImage], referer: str,
    ) -> ImageBatch:
        """Save each main photo independently and report partial failures."""
        if site not in SITES or marketplace not in _MARKETPLACES or not product_id:
            raise ValueError("Неизвестный сайт, маркетплейс или ID товара.")
        folder = self.root / site / "images" / _safe_product_id(product_id) / _MARKETPLACES[marketplace]
        saved: list[SavedImage] = []
        failed: list[ImageFailure] = []
        for position, product in enumerate(products, 1):
            self._check_cancelled()
            try:
                payload, extension = self._download(product.image_url, marketplace, referer)
                path = folder / f"{position}{extension}"
                self._check_cancelled()
                self._save(path, payload)
            except ImageDownloadError as error:
                if isinstance(error, ImageDownloadCancelled):
                    raise
                failed.append(ImageFailure(position, product.image_url, str(error)))
            else:
                saved.append(SavedImage(
                    product_id, marketplace, position, product.image_url, path, len(payload),
                ))
        return ImageBatch(tuple(saved), tuple(failed))
