"""Bounded client for 2Captcha's Yandex SmartCaptcha coordinate task."""

from __future__ import annotations

import base64
import json
import time
from typing import Callable
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


_API_ROOT = "https://api.2captcha.com/"
_REQUEST_TIMEOUT = 15.0
_SOLVE_TIMEOUT = 120.0
_POLL_INTERVAL = 5.0
_CANCEL_INTERVAL = 0.25
_MAX_RESPONSE_BYTES = 1024 * 1024


class CaptchaServiceError(RuntimeError):
    """The captcha service could not return a usable solution."""


class CaptchaServiceCancelled(CaptchaServiceError):
    """The user cancelled the captcha request."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API key to a redirect destination.
        return None


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise CaptchaServiceCancelled("Решение капчи отменено.")


def _post(endpoint: str, payload: dict, timeout: float) -> dict:
    request = Request(
        _API_ROOT + endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        result = json.loads(raw)
    except (URLError, OSError, ValueError, TypeError, UnicodeError):
        # Network exceptions can contain request data; do not expose their details.
        raise CaptchaServiceError("Не удалось получить корректный ответ 2Captcha.") from None
    if not isinstance(result, dict):
        raise CaptchaServiceError("2Captcha вернул некорректный ответ.")
    error_id = result.get("errorId")
    if type(error_id) is not int:
        raise CaptchaServiceError("2Captcha вернул некорректный ответ.")
    if error_id != 0:
        raise CaptchaServiceError(f"2Captcha вернул ошибку (код {error_id}).")
    return result


def _wait(seconds: float, cancelled: Callable[[], bool]) -> None:
    deadline = time.monotonic() + seconds
    while True:
        _check_cancelled(cancelled)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(_CANCEL_INTERVAL, remaining))


def solve_yandex_coordinates(
    image: bytes, instructions: bytes, *, api_key: str, cancelled: Callable[[], bool],
) -> list[tuple[int, int]]:
    """Send the two images and return ordered coordinates in image pixels.

    Network requests have a bounded timeout; cancellation is checked between them
    and during polling. The service may charge for a task once it is submitted.
    """
    _check_cancelled(cancelled)
    if not isinstance(api_key, str) or not api_key.strip():
        raise CaptchaServiceError("Укажите API-ключ 2Captcha.")
    if not isinstance(image, bytes) or not image or not isinstance(instructions, bytes) or not instructions:
        raise CaptchaServiceError("Отсутствует изображение капчи или инструкции.")
    api_key = api_key.strip()
    deadline = time.monotonic() + _SOLVE_TIMEOUT
    result = _post("createTask", {
        "clientKey": api_key,
        "task": {
            "type": "SmartCaptchaTask",
            "image": base64.b64encode(image).decode("ascii"),
            "imgInstructions": base64.b64encode(instructions).decode("ascii"),
            "comment": "select objects in the order of the instruction",
        },
    }, min(_REQUEST_TIMEOUT, _SOLVE_TIMEOUT))
    _check_cancelled(cancelled)
    task_id = result.get("taskId")
    if type(task_id) is not int or task_id <= 0:
        raise CaptchaServiceError("2Captcha не вернул идентификатор задачи.")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptchaServiceError("Истекло время ожидания решения капчи 2Captcha.")
        _wait(min(_POLL_INTERVAL, remaining), cancelled)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptchaServiceError("Истекло время ожидания решения капчи 2Captcha.")
        result = _post("getTaskResult", {"clientKey": api_key, "taskId": task_id},
                       min(_REQUEST_TIMEOUT, remaining))
        _check_cancelled(cancelled)
        status = result.get("status")
        if status == "processing":
            continue
        if status != "ready":
            raise CaptchaServiceError("2Captcha вернул неизвестный статус задачи.")
        solution = result.get("solution")
        points = solution.get("coordinates") if isinstance(solution, dict) else None
        if not isinstance(points, list) or not points:
            raise CaptchaServiceError("2Captcha вернул некорректные координаты.")
        coordinates = []
        for point in points:
            if not isinstance(point, dict):
                raise CaptchaServiceError("2Captcha вернул некорректные координаты.")
            x, y = point.get("x"), point.get("y")
            if type(x) is not int or type(y) is not int or x < 0 or y < 0:
                raise CaptchaServiceError("2Captcha вернул некорректные координаты.")
            coordinates.append((x, y))
        return coordinates
