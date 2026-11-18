"""Publish one site's parsed search results and original photos over FTP."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from ftplib import FTP, all_errors, error_perm, error_proto, error_reply, error_temp
from io import BytesIO
import json
from pathlib import Path
import re
from time import sleep
from typing import Any
from uuid import uuid4

from web_parser.config import SITES
from web_parser.images import DEFAULT_IMAGE_ROOT, SavedImage
from web_parser.ozon import OzonProduct
from web_parser.product_links import ProductLinks
from web_parser.wildberries import WildberriesProduct
from web_parser.yandex_market import YandexMarketProduct


_MARKETPLACES = (
    ("ozon", "Ozon", "ozon_url"),
    ("wildberries", "Wildberries", "wildberries_url"),
    ("yandex_market", "Яндекс Маркет", "yandex_market_url"),
)
_Product = OzonProduct | WildberriesProduct | YandexMarketProduct


class FTPExportError(RuntimeError):
    """The FTP server did not receive a complete, consistent export."""


class FTPExportCancelled(FTPExportError):
    """The user stopped the upload before the JSON was published."""


_FTP_REPLY_REASONS = {
    "421": "сервер временно недоступен",
    "425": "не удалось открыть соединение для передачи данных",
    "426": "передача файла прервана",
    "452": "на сервере недостаточно места",
    "530": "не удалось войти на сервер",
    "550": "сервер отказал в доступе к файлу или каталогу",
    "552": "превышен лимит хранения на сервере",
}


def _transfer_error(stage: str, error: Exception, attempts: int) -> FTPExportError:
    """Describe the failing stage without copying server text or credentials."""
    if isinstance(error, (error_reply, error_temp, error_perm, error_proto)):
        match = re.match(r"(\d{3})\b", str(error))
        code = match.group(1) if match else None
        reason = (
            f"FTP {code}: {_FTP_REPLY_REASONS.get(code, 'сервер отклонил команду')}"
            if code else "некорректный ответ FTP-сервера"
        )
    elif isinstance(error, TimeoutError):
        reason = "превышено время ожидания"
    elif isinstance(error, ConnectionRefusedError):
        reason = "соединение отклонено"
    elif isinstance(error, ConnectionResetError):
        reason = "соединение сброшено"
    elif isinstance(error, BrokenPipeError):
        reason = "соединение разорвано"
    elif isinstance(error, EOFError):
        reason = "сервер закрыл соединение"
    elif isinstance(error, OSError):
        code = getattr(error, "winerror", None) or error.errno
        reason = f"сетевая ошибка (код {code})" if code is not None else "ошибка соединения"
    else:
        reason = "ошибка передачи"
    return FTPExportError(f"{stage}: {reason}. Попыток: {attempts}.")


@dataclass(frozen=True, slots=True)
class FTPExportResult:
    remote_json: str
    uploaded_images: int
    products: int


def _path_parts(path: str) -> tuple[bool, tuple[str, ...]]:
    if not isinstance(path, str) or "\\" in path or "\x00" in path:
        raise FTPExportError("Каталог FTP должен быть путём с разделителем «/».")
    absolute = path.startswith("/")
    parts = tuple(part for part in path.split("/") if part)
    if not parts and not absolute:
        raise FTPExportError("Укажите базовый каталог FTP; для корня используйте «/».")
    if any(part in (".", "..") or any(ord(char) < 32 for char in part) for part in parts):
        raise FTPExportError("Укажите базовый каталог FTP без «.» и «..».")
    if not parts:
        # A lone slash means the directory where this account lands after login.
        absolute = False
    return absolute, parts


def _image_relative_path(site: str, image: SavedImage, image_root: Path) -> str:
    try:
        relative = image.path.resolve(strict=True).relative_to((image_root / site).resolve())
    except (OSError, ValueError) as error:
        raise FTPExportError("Сохранённое фото отсутствует или лежит вне каталога сайта.") from error
    parts = relative.parts
    marketplace = next((slug for slug, label, _ in _MARKETPLACES if label == image.marketplace), None)
    if (
        len(parts) != 4 or parts[0] != "images" or parts[2] != marketplace
        or not parts[1] or parts[3] != f"{image.position}{image.path.suffix}"
        or image.product_id == "" or image.position < 1
    ):
        raise FTPExportError("Неверный путь сохранённого фото.")
    return "/".join(parts)


def _remote_image_path(local_path: str, image_directory: str) -> str:
    return f"{image_directory}/{local_path.removeprefix('images/')}"


def _product_document(product: _Product, image_path: str | None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "product_url": product.product_url,
        "name": product.name,
        "price": str(product.price),
        "discounted_price": str(product.discounted_price),
        "rating": str(product.rating) if product.rating is not None else None,
        "reviews": product.reviews,
        "image_url": product.image_url,
        "image_path": image_path,
    }
    if isinstance(product, WildberriesProduct):
        document["wb_id"] = product.wb_id
    return document


def build_document(
    site: str,
    links: Sequence[ProductLinks],
    results: Mapping[str, Mapping[str, Sequence[_Product]]],
    saved_images: Mapping[tuple[str, str], Sequence[SavedImage]],
    *, image_root: Path = DEFAULT_IMAGE_ROOT,
    generated_at: datetime | None = None,
    image_directory: str = "images",
) -> dict[str, Any]:
    """Describe only results from this run; image paths are relative to the site directory."""
    if site not in SITES:
        raise FTPExportError("Неизвестный сайт для выгрузки.")
    photo_paths = {
        (product_id, marketplace, image.position): _remote_image_path(
            _image_relative_path(site, image, image_root), image_directory,
        )
        for (product_id, marketplace), batch in saved_images.items()
        for image in batch
    }
    rows: list[dict[str, Any]] = []
    for link in links:
        marketplaces: dict[str, Any] = {}
        for slug, label, url_field in _MARKETPLACES:
            products = results.get(label, {}).get(link.product_id, ())
            marketplaces[slug] = {
                "search_url": getattr(link, url_field),
                "status": "ok" if link.product_id in results.get(label, {}) else "error",
                "products": [
                    _product_document(product, photo_paths.get((link.product_id, label, position)))
                    for position, product in enumerate(products, 1)
                ],
            }
        rows.append({"id": link.product_id, "marketplaces": marketplaces})
    moment = generated_at or datetime.now(timezone.utc)
    return {"site": site, "generated_at": moment.isoformat(), "products": rows}


def _enter_directory(ftp: FTP, home: str, absolute: bool, parts: tuple[str, ...]) -> None:
    ftp.cwd("/" if absolute else home)
    for part in parts:
        try:
            ftp.cwd(part)
        except error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def _publish_json(ftp: FTP, run_id: str) -> None:
    temporary = f".processed_products.json.uploading-{run_id}"
    final = "processed_products.json"
    backup = f".processed_products.json.backup-{run_id}"
    try:
        ftp.rename(temporary, final)
        return
    except error_perm:
        # Servers differ on whether RNTO may replace an existing file.
        ftp.rename(final, backup)
    try:
        ftp.rename(temporary, final)
    except all_errors:
        try:
            ftp.rename(backup, final)
        except all_errors:
            pass
        raise
    try:
        ftp.delete(backup)
    except error_perm:
        pass


def export_run(
    site: str,
    links: Sequence[ProductLinks],
    results: Mapping[str, Mapping[str, Sequence[_Product]]],
    saved_images: Mapping[tuple[str, str], Sequence[SavedImage]],
    settings: Mapping[str, object],
    *, image_root: Path = DEFAULT_IMAGE_ROOT,
    cancel_requested: Callable[[], bool] = lambda: False,
) -> FTPExportResult:
    """Upload photos to a new run directory, then publish processed_products.json."""
    if site not in SITES:
        raise FTPExportError("Неизвестный сайт для выгрузки.")
    host = settings.get("host")
    port = settings.get("port")
    username = settings.get("username")
    password = settings.get("password")
    if (
        not isinstance(host, str) or not host.strip()
        or any(char in host for char in "/\\:@")
        or any(char.isspace() for char in host)
    ):
        raise FTPExportError("Укажите корректный адрес FTP-сервера.")
    if not isinstance(port, int) or isinstance(port, bool):
        raise FTPExportError("Укажите корректный порт FTP-сервера.")
    if not 1 <= port <= 65535:
        raise FTPExportError("Укажите корректный порт FTP-сервера.")
    if not isinstance(username, str) or not isinstance(password, str):
        raise FTPExportError("Укажите логин и пароль FTP.")
    remote_path = settings.get("remote_path")
    if not isinstance(remote_path, str):
        raise FTPExportError("Укажите базовый каталог FTP.")
    absolute, base = _path_parts(remote_path)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    image_directory = f"images/{run_id}"

    def check_cancelled(_block: bytes | None = None) -> None:
        if cancel_requested():
            raise FTPExportCancelled("Выгрузка на FTP остановлена пользователем.")

    for attempt in (1, 2):
        ftp = FTP(timeout=20)
        uploaded = 0
        stage = "подключение"
        publishing = False
        retry = False
        try:
            check_cancelled()
            ftp.connect(host.strip(), port, timeout=20)
            stage = "вход на сервер"
            ftp.login(username, password)
            ftp.set_pasv(True)
            home = ftp.pwd()
            for batch in saved_images.values():
                for image in batch:
                    check_cancelled()
                    relative = _image_relative_path(site, image, image_root)
                    parts = tuple(_remote_image_path(relative, image_directory).split("/"))
                    stage = f"подготовка каталога фото (ID {image.product_id})"
                    _enter_directory(ftp, home, absolute, (*base, *parts[:-1]))
                    stage = f"передача фото (ID {image.product_id}, №{image.position})"
                    try:
                        source = image.path.open("rb")
                    except OSError as error:
                        raise FTPExportError("Сохранённое фото недоступно для чтения.") from error
                    with source:
                        ftp.storbinary(f"STOR {parts[-1]}", source, callback=check_cancelled)
                    uploaded += 1
            check_cancelled()
            document = build_document(
                site, links, results, saved_images,
                image_root=image_root, image_directory=image_directory,
            )
            payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            stage = "подготовка каталога JSON"
            _enter_directory(ftp, home, absolute, base)
            stage = "передача временного JSON"
            ftp.storbinary(
                f"STOR .processed_products.json.uploading-{run_id}", BytesIO(payload),
                callback=check_cancelled,
            )
            check_cancelled()
            stage = "публикация JSON"
            publishing = True
            _publish_json(ftp, run_id)
            prefix = f"{home.rstrip('/')}/" if not base else "/" if absolute else ""
            remote_json = prefix + "/".join((*base, "processed_products.json"))
            return FTPExportResult(remote_json, uploaded, len(document["products"]))
        except FTPExportError:
            raise
        except error_perm as error:
            raise _transfer_error(stage, error, attempt) from error
        except all_errors as error:
            if attempt == 1 and not publishing:
                retry = True
            else:
                raise _transfer_error(stage, error, attempt) from error
        finally:
            try:
                ftp.close()
            except OSError:
                pass
        if retry:
            check_cancelled()
            sleep(1)
    raise AssertionError("Выгрузка FTP завершилась без результата или ошибки.")
