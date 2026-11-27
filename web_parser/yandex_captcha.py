"""Solve the silhouette variant of Yandex SmartCaptcha through 2Captcha.

2Captcha expects the original 320x180 image and 480x180 instruction image;
other challenge types deliberately remain unsupported.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Callable
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, Page
from web_parser.captcha_service import CaptchaServiceCancelled, solve_yandex_coordinates

_FRAME_HOSTS = ("yandex.ru", "yandex.net", "yandexcloud.net", "yandexcloud.ru", "yastatic.net")
_IMAGE_LIMIT = 2 * 1024 * 1024
_WAIT_STEPS = 40


def _trusted_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        return (parts.scheme == "https" and not parts.username and not parts.password
                and (parts.port is None or parts.port == 443)
                and any(host == name or host.endswith("." + name) for name in _FRAME_HOSTS))
    except ValueError:
        return False


def _cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise CaptchaServiceCancelled("Решение капчи отменено.")


def _ready(page: Page) -> bool:
    # Require an actual result/empty state; a hidden widget alone is not proof.
    return bool(page.evaluate(r"""() => {
        const visible = e => {
            const rect = e.getBoundingClientRect();
            const style = getComputedStyle(e);
            return rect.width > 0 && rect.height > 0 &&
                style.display !== 'none' && style.visibility !== 'hidden';
        };
        const text = document.body?.innerText || '';
        const title = document.title || '';
        if (/\/(showcaptcha|captcha)\/?$/i.test(location.pathname) ||
            /^(captcha|капча)(\s|$)|подтвердите.*не робот|проверка.*(?:безопасности|робот)/i.test(title) ||
            /(?:подтвердите|докажите).{0,100}(?:не робот|человек)|запросы.{0,100}похожи на автоматические/i
                .test(text) ||
            [...document.querySelectorAll(
                '.smart-captcha, iframe[src*="captcha" i], form[action*="showcaptcha"]'
            )].some(visible)) {
            return false;
        }
        return !!document.querySelector('[data-auto="searchOrganic"]') ||
            /ничего не найдено|товары не найдены|ничего не нашлось/i.test(text);
    }"""))


def _images(frame):
    """Find the two images by the dimensions documented for SmartCaptchaTask."""
    main = instruction = None
    for img in frame.locator("img").all():
        if not img.is_visible():
            continue
        metadata = img.evaluate("""e => ({src: e.currentSrc || e.src,
            width: e.naturalWidth, height: e.naturalHeight})""")
        if not isinstance(metadata, dict):
            continue
        size = (metadata.get("width"), metadata.get("height"))
        if size == (320, 180):
            if main is not None:
                return None
            main = (img, metadata)
        elif size == (480, 180):
            if instruction is not None:
                return None
            instruction = (img, metadata)
    return (main, instruction) if main and instruction else None


def _read_image(page: Page, url: str) -> bytes | None:
    if url.startswith("data:image/"):
        try:
            header, payload = url.split(",", 1)
            if not header.lower().endswith(";base64") or len(payload) > _IMAGE_LIMIT * 2:
                return None
            result = base64.b64decode(payload, validate=True)
            return result if 0 < len(result) <= _IMAGE_LIMIT else None
        except (ValueError, binascii.Error):
            return None
    if not _trusted_url(url):
        return None
    # Request images only from Yandex, without following redirects. Cookies stay
    # in the browser context and only the two image bytes go to the solver.
    response = page.request.get(url, timeout=5000, max_redirects=0)
    if not response.ok or not response.headers.get("content-type", "").lower().startswith("image/"):
        return None
    content_length = response.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > _IMAGE_LIMIT:
        return None
    body = response.body()
    return body if 0 < len(body) <= _IMAGE_LIMIT else None


def _same_image(element, expected: dict) -> bool:
    return element.is_visible() and element.evaluate("""e => ({src: e.currentSrc || e.src,
        width: e.naturalWidth, height: e.naturalHeight})""") == expected


def _wait_ready(page: Page, cancelled: Callable[[], bool], steps: int) -> bool:
    for _ in range(steps):
        _cancel(cancelled)
        if _ready(page):
            return True
        page.wait_for_timeout(250)
    return False


def solve_yandex_captcha(page: Page, *, api_key: str, cancelled: Callable[[], bool]) -> bool:
    """Try one checkbox and one silhouette challenge in the current session.

    Success requires the Market results (or its empty-result state) to appear.
    Unsupported, changed, and rejected challenges return False. Service errors
    propagate; cancellation remains distinguishable from other service errors.
    """
    if not api_key:
        return False
    try:
        _cancel(cancelled)
        if _ready(page):
            return True
        frames = [f for f in page.frames if _trusted_url(f.url)]
        challenge = next(((f, pair) for f in frames if (pair := _images(f))), None)
        if challenge is None:
            # SmartCaptcha's basic check may immediately validate or reveal its
            # additional task. Do not click unrelated checkboxes on Market.
            for frame in frames:
                if "captcha" not in frame.url.lower():
                    continue
                checkbox = frame.locator(
                    '[role="checkbox"], input[type="checkbox"], '
                    '.CheckboxCaptcha-Button, .CaptchaButton-ProgressWrapper',
                )
                for element in checkbox.all():
                    if element.is_visible():
                        element.click(timeout=3000)
                        break
                else:
                    continue
                break
            for _ in range(_WAIT_STEPS):
                _cancel(cancelled)
                if _ready(page):
                    return True
                challenge = next(((f, pair) for f in page.frames if _trusted_url(f.url)
                                  if (pair := _images(f))), None)
                if challenge is not None:
                    break
                page.wait_for_timeout(250)
        if challenge is None:
            return False
        _, ((main, main_meta), (instruction, instruction_meta)) = challenge
        image = _read_image(page, main_meta["src"])
        instructions = _read_image(page, instruction_meta["src"])
        if not image or not instructions:
            return False
        main_snapshot = main.screenshot(timeout=3000)
        instruction_snapshot = instruction.screenshot(timeout=3000)
        _cancel(cancelled)
        points = solve_yandex_coordinates(image, instructions, api_key=api_key, cancelled=cancelled)
        _cancel(cancelled)
        if not _same_image(main, main_meta) or not _same_image(instruction, instruction_meta):
            return False
        # A challenge can refresh while retaining its URL. Compare the visible
        # image pixels as well; screenshots are never sent to the service.
        if (main_snapshot != main.screenshot(timeout=3000)
                or instruction_snapshot != instruction.screenshot(timeout=3000)):
            return False
        box = main.bounding_box()
        if (not box or box["width"] <= 0 or box["height"] <= 0
                or abs(box["width"] / box["height"] - 320 / 180) > 0.02):
            return False
        if not points or any(type(x) is not int or type(y) is not int or not (0 <= x < 320 and 0 <= y < 180)
                             for x, y in points):
            return False
        for x, y in points:
            _cancel(cancelled)
            if not _same_image(main, main_meta) or not _same_image(instruction, instruction_meta):
                return False
            main.click(position={"x": x * box["width"] / 320, "y": y * box["height"] / 180}, timeout=3000)
        # Some variants submit automatically; others display an explicit button.
        frame = challenge[0]
        submit = frame.get_by_role(
            "button", name=re.compile(r"^(Отправить|Подтвердить|Проверить|Submit|Verify)$", re.I),
        )
        if submit.count() and submit.first.is_visible() and submit.first.is_enabled():
            submit.first.click(timeout=3000)
        else:
            progress = frame.locator(".CaptchaButton-ProgressWrapper")
            if progress.count() and progress.first.is_visible():
                progress.first.click(timeout=3000)
        return _wait_ready(page, cancelled, _WAIT_STEPS)
    except PlaywrightError:
        return False
