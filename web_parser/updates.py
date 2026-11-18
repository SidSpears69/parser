"""Check public GitHub releases and prepare a verified Windows installer."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import mkdtemp
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


REPOSITORY = "SidSpears69/parser"
RELEASE_FEED = f"https://raw.githubusercontent.com/{REPOSITORY}/release-feed/update.json"
RELEASE_MANIFEST = f"https://github.com/{REPOSITORY}/releases/latest/download/update.json"
MAX_INSTALLER_BYTES = 1_500_000_000
_VERSION = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class UpdateError(RuntimeError):
    """A release cannot be checked or safely downloaded."""


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    tag: str
    filename: str
    url: str
    size: int
    sha256: str


def version_tuple(version: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(version)
    if match is None:
        raise UpdateError(f"Некорректный номер версии: {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def installed_version() -> str | None:
    """Return the release stamped into a packaged build, if present."""
    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve().parent / "version.json"
    try:
        version = json.loads(path.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(version, str):
        return None
    try:
        version_tuple(version)
    except UpdateError:
        return None
    return version


def _request(url: str, *, accept: str = "application/json") -> Request:
    return Request(url, headers={
        "Accept": accept,
        "User-Agent": "WebParser-Updater",
    })


def _read_release_manifest(url: str) -> object:
    with urlopen(_request(url), timeout=15) as response:
        payload = response.read(64_001)
    if len(payload) > 64_000:
        raise UpdateError("Ответ GitHub слишком большой.")
    try:
        return json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise UpdateError("GitHub вернул некорректные данные релиза.") from error


def latest_release() -> Release:
    """Read the static release feed, falling back to the release asset if unavailable."""
    try:
        release = _read_release_manifest(RELEASE_FEED)
    except OSError:
        try:
            release = _read_release_manifest(RELEASE_MANIFEST)
        except HTTPError as error:
            if error.code in (403, 429):
                raise UpdateError(
                    "GitHub временно ограничил проверку обновлений. "
                    "Попробуйте позже или скачайте установщик со страницы релизов."
                ) from error
            raise UpdateError(f"Не удалось проверить релизы GitHub: HTTP {error.code}.") from error
        except OSError as error:
            raise UpdateError(f"Не удалось проверить релизы GitHub: {error}") from error
    if not isinstance(release, dict):
        raise UpdateError("GitHub вернул некорректные данные релиза.")
    version = release.get("version")
    tag = release.get("tag_name")
    if not isinstance(version, str) or not isinstance(tag, str) or tag != f"v{version}":
        raise UpdateError("У релиза нет номера версии вида v1.2.3.")
    version_tuple(version)
    filename = f"WebParserSetup-{version}.exe"
    if release.get("filename") != filename:
        raise UpdateError("В данных релиза указано неверное имя установщика.")
    digest = release.get("sha256")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise UpdateError("В данных релиза нет SHA-256 установщика.")
    size = release.get("size")
    if type(size) is not int or not 0 < size <= MAX_INSTALLER_BYTES:
        raise UpdateError("Некорректный размер установщика.")
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
    return Release(version, tag, filename, url, size, digest)


def is_newer(release: Release, current_version: str) -> bool:
    return version_tuple(release.version) > version_tuple(current_version)


def download_installer(
    release: Release, progress: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    """Download to a fresh temp directory and verify size and SHA-256."""
    try:
        folder = Path(mkdtemp(prefix="webparser-update-"))
    except OSError as error:
        raise UpdateError(f"Не удалось подготовить загрузку установщика: {error}") from error
    destination = folder / release.filename
    digest = hashlib.sha256()
    received = 0
    try:
        with (
            urlopen(_request(release.url, accept="application/octet-stream"), timeout=30) as response,
            destination.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                if cancel_requested is not None and cancel_requested():
                    raise UpdateError("Загрузка отменена.")
                received += len(chunk)
                if received > release.size or received > MAX_INSTALLER_BYTES:
                    raise UpdateError("Размер загружаемого установщика не совпадает с релизом.")
                output.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(received, release.size)
        if received != release.size or digest.hexdigest() != release.sha256:
            raise UpdateError("Контрольная сумма установщика не совпадает с релизом.")
        return destination
    except (OSError, UpdateError) as error:
        try:
            destination.unlink(missing_ok=True)
            folder.rmdir()
        except OSError:
            pass
        if isinstance(error, UpdateError):
            raise
        raise UpdateError(f"Не удалось загрузить установщик: {error}") from error
