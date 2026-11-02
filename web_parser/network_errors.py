"""Short, credential-free descriptions of Playwright request failures."""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError


_NETWORK_CODE = re.compile(r"\b(?:E(?:CONN|HOST|NET|PIPE|TIMEDOUT|AI_|NOTFOUND)[A-Z_]*|ERR_[A-Z_]+)\b")


def request_failure_reason(error: PlaywrightError) -> str:
    """Keep the useful error class without copying URLs or proxy credentials to logs."""
    message = str(error)
    if "Socks5 proxy rejected connection" in message:
        return "SOCKS5-прокси отклонил соединение"
    if "Invalid character in header content" in message:
        return "недопустимый символ в HTTP-заголовке"
    if "Target page, context or browser has been closed" in message:
        return "браузер или контекст запроса закрыт"
    code = _NETWORK_CODE.search(message)
    if code is not None:
        return f"сетевая ошибка {code.group(0)}"
    if "Timeout" in message or "timeout" in message:
        return "превышено время ожидания"
    return "ошибка сетевого запроса Playwright"
