"""Capture matching product JSON responses from Wildberries in a browser tab."""

from collections import deque
from contextlib import suppress
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError, Response

from web_parser.browser_access.checks import (
    AUTH_OR_RATE_LIMIT_STATUSES, raise_if_captcha, retryable_status, validate_url,
)
from web_parser.browser_access.models import BrowserAccessDenied, BrowserAccessError, BrowserProxyConnectionFailed
from web_parser.browser_access.navigation import BrowserNavigation


_WILDBERRIES_JSON_FIRST_WAIT_SECONDS = 45
_WILDBERRIES_JSON_RETRY_WAIT_SECONDS = 10


class WildberriesBrowserSession(BrowserNavigation):
    """A browser session that can collect Wildberries product responses."""

    def fetch_wildberries_json(self, url: str) -> object:
        """Capture search JSON even when the initial page response is HTTP 498."""
        url = validate_url(url)
        page = self._page
        if page is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")
        documents: list[object] = []
        candidate_responses: deque[Response] = deque()
        denied_json_status: int | None = None
        requested_parts = urlsplit(url)
        requested_query = parse_qs(requested_parts.query)
        search_query = requested_query.get("search", [""])[0]
        brand_match = re.fullmatch(
            r"/brands/(\d+)(?:-[^/]*)?/?", requested_parts.path, re.IGNORECASE,
        )

        def is_product_json(response_url: str) -> bool:
            parts = urlsplit(response_url)
            if parts.hostname not in {
                "www.wildberries.ru", "u-search.wb.ru", "search.wb.ru", "u-catalog.wb.ru",
            }:
                return False
            parameters = parse_qs(parts.query)
            if brand_match is not None:
                if not re.search(r"/brands/v\d+/catalog\Z", parts.path):
                    return False
                if parameters.get("brand") != [brand_match.group(1)]:
                    return False
                if parameters.get("page", ["1"]) != requested_query.get("page", ["1"]):
                    return False
                for key, values in requested_query.items():
                    if (key == "sort" or re.fullmatch(r"f\d+", key)) and parameters.get(key) != values:
                        return False
                return True
            return (
                "/exactmatch/" in parts.path
                and parts.path.endswith("/search")
                and parameters.get("query", [""])[0] == search_query
            )

        def capture(response: Response) -> None:
            nonlocal denied_json_status
            if response.status == 200 and is_product_json(response.url):
                candidate_responses.append(response)
            elif (
                self._strict_access and response.status in (401, 403, 429, 498)
                and denied_json_status not in AUTH_OR_RATE_LIMIT_STATUSES
                and is_product_json(response.url)
            ):
                denied_json_status = response.status

        def read_candidates() -> None:
            while candidate_responses:
                response = candidate_responses.popleft()
                try:
                    document = json.loads(response.body())
                except (PlaywrightError, UnicodeError, ValueError):
                    continue
                if not isinstance(document, dict):
                    continue
                products = document.get("products")
                data = document.get("data")
                if products is None and isinstance(data, dict):
                    products = data.get("products")
                if isinstance(products, list):
                    documents.append(document)
            if denied_json_status is not None:
                raise BrowserAccessDenied(denied_json_status, "сервер ограничил доступ к JSON Wildberries.")

        page.on("response", capture)
        try:
            last_status: int | None = None
            last_problem = "не появился JSON товаров"
            for attempt in range(1, self.attempts + 1):
                self._begin_navigation()
                try:
                    navigation_response = self._navigate(page, url, wait_until="domcontentloaded")
                except (PlaywrightError, BrowserProxyConnectionFailed) as error:
                    read_candidates()
                    if documents:
                        return documents[-1]
                    if isinstance(error, BrowserProxyConnectionFailed):
                        raise
                    last_problem = f"ошибка браузера: {self._safe_error(error)}"
                    continue
                if navigation_response is not None:
                    self._check_access_response(page, navigation_response.status)
                read_candidates()
                if documents:
                    return documents[-1]
                if self._strict_access and navigation_response is not None:
                    raise_if_captcha(page, navigation_response.status)
                if navigation_response is None:
                    last_problem = "пустой ответ браузера"
                    continue
                last_status = navigation_response.status
                if not navigation_response.ok and navigation_response.status not in (403, 498):
                    last_problem = f"HTTP {navigation_response.status}"
                    if retryable_status(navigation_response.status):
                        continue
                    raise BrowserAccessError(f"Сервер вернул {last_problem}.")
                wait_seconds = (
                    _WILDBERRIES_JSON_FIRST_WAIT_SECONDS if attempt == 1
                    else _WILDBERRIES_JSON_RETRY_WAIT_SECONDS
                )
                deadline = time.monotonic() + wait_seconds
                while not documents and time.monotonic() < deadline:
                    self._check_cancelled()
                    try:
                        page.wait_for_timeout(1_000)
                    except PlaywrightError as error:
                        self._check_browser_interruption(error)
                        last_problem = f"ошибка браузера: {self._safe_error(error)}"
                        break
                    read_candidates()
                    if self._strict_access and not documents:
                        raise_if_captcha(page, last_status)
                if documents:
                    return documents[-1]
                if self._strict_access and last_status in (403, 498):
                    self._check_access_response(page, last_status)
                    raise_if_captcha(page, last_status)
                    break
            if last_status in (403, 498):
                raise BrowserAccessDenied(
                    last_status, "в браузере не появился JSON товаров Wildberries.",
                )
            raise BrowserAccessError(
                f"В открытой выдаче Wildberries {last_problem}. Попыток: {self.attempts}."
            )
        finally:
            with suppress(PlaywrightError):
                page.remove_listener("response", capture)
