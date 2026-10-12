"""Download and validate per-site product links from a token-protected JSON feed."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_TIMEOUT_SECONDS = 30


class ProductLinksError(ValueError):
    """The product-list response could not be downloaded or parsed."""


@dataclass(frozen=True, slots=True)
class ProductLinks:
    product_id: str
    ozon_url: str
    wildberries_url: str
    yandex_market_url: str


@dataclass(frozen=True, slots=True)
class ProductLinkFields:
    """Names and nesting supplied by the site's JSON contract."""

    items_key: str | None
    product_id_key: str
    ozon_key: str
    wildberries_key: str
    yandex_market_key: str
    marketplaces_key: str | None = None
    grouped_by_product_id: bool = False


MARKETPLACE_FEED_FIELDS = ProductLinkFields(
    items_key=None,
    product_id_key="id",
    ozon_key="ozon",
    wildberries_key="wildberries",
    yandex_market_key="yandex_market",
    marketplaces_key="marketplaces",
    grouped_by_product_id=True,
)


def _http_url(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductLinksError(f"{location}: ожидается непустая ссылка.")
    link = value.strip()
    try:
        parsed = urlparse.urlsplit(link)
        valid = parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)
        if parsed.username or parsed.password:
            valid = False
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ProductLinksError(f"{location}: ожидается HTTP(S)-ссылка без учётных данных.")
    return link


def parse_product_links(document: object, fields: ProductLinkFields) -> list[ProductLinks]:
    """Validate every record and preserve the feed order for later parsing."""
    if fields.grouped_by_product_id:
        if not isinstance(document, dict):
            raise ProductLinksError("Список товаров в JSON должен быть объектом с массивами по ID.")
        records = []
        for group_id, group in document.items():
            if not isinstance(group, list):
                raise ProductLinksError(f"Товар {group_id}: ожидается массив записей.")
            records.extend(group)
    elif fields.items_key is not None:
        if not isinstance(document, dict) or fields.items_key not in document:
            raise ProductLinksError(f"В JSON отсутствует массив «{fields.items_key}».")
        records = document[fields.items_key]
    else:
        records = document
    if not isinstance(records, list):
        raise ProductLinksError("Список товаров в JSON должен быть массивом.")

    links: list[ProductLinks] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ProductLinksError(f"Товар №{index}: ожидается объект JSON.")
        raw_id = record.get(fields.product_id_key)
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            raise ProductLinksError(f"Товар №{index}: неверный ID.")
        product_id = str(raw_id).strip()
        if not product_id:
            raise ProductLinksError(f"Товар №{index}: пустой ID.")
        if product_id in seen_ids:
            raise ProductLinksError(f"Товар №{index}: повторяющийся ID {product_id}.")
        seen_ids.add(product_id)
        if fields.marketplaces_key is None:
            marketplaces = record
        else:
            nested_marketplaces = record.get(fields.marketplaces_key)
            if not isinstance(nested_marketplaces, dict):
                raise ProductLinksError(f"Товар {product_id}: отсутствует объект «{fields.marketplaces_key}».")
            marketplaces = nested_marketplaces
        links.append(ProductLinks(
            product_id=product_id,
            ozon_url=_http_url(marketplaces.get(fields.ozon_key), f"Товар {product_id}, OZON"),
            wildberries_url=_http_url(marketplaces.get(fields.wildberries_key), f"Товар {product_id}, Wildberries"),
            yandex_market_url=_http_url(marketplaces.get(fields.yandex_market_key), f"Товар {product_id}, Яндекс Маркет"),
        ))
    return links


class _SameOriginRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        try:
            old = urlparse.urlsplit(request.full_url)
            new = urlparse.urlsplit(new_url)
            same_origin = (new.scheme.lower(), new.hostname, new.port) == (
                old.scheme.lower(), old.hostname, old.port,
            )
        except ValueError:
            same_origin = False
        if not same_origin:
            raise ProductLinksError("Источник JSON перенаправляет запрос на другой сервер.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def download_json(source_url: str, headers: Mapping[str, str]) -> object:
    """Download a bounded UTF-8 JSON response without exposing request headers in errors."""
    url = _http_url(source_url, "URL списка товаров")
    if urlparse.urlsplit(url).scheme.lower() != "https":
        raise ProductLinksError("Для доступа к JSON с токеном требуется HTTPS.")
    request = urlrequest.Request(url, headers=dict(headers), method="GET")
    opener = urlrequest.build_opener(_SameOriginRedirect())
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except ProductLinksError:
        raise
    except urlerror.HTTPError as error:
        raise ProductLinksError(f"Источник JSON вернул HTTP {error.code}.") from error
    except (urlerror.URLError, TimeoutError, OSError) as error:
        raise ProductLinksError("Не удалось соединиться с источником JSON.") from error
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ProductLinksError("Ответ JSON превышает допустимый размер 64 МБ.")
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProductLinksError("Источник вернул некорректный JSON в кодировке UTF-8.") from error


def fetch_product_links(source_url: str, token: str) -> list[ProductLinks]:
    """Load the marketplace feed using the site's Bearer token."""
    token = token.strip()
    if not token or any(character.isspace() for character in token):
        raise ProductLinksError("Укажите корректный токен доступа.")
    document = download_json(source_url, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
    return parse_product_links(document, MARKETPLACE_FEED_FIELDS)
