"""Find a Windows YandexDriver release compatible with an installed Yandex Browser."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from threading import Lock
import urllib.error
import urllib.parse
import urllib.request
import zipfile


_RELEASES_URL = "https://api.github.com/repos/yandex/YandexDriver/releases"
_BROWSER_VERSION = re.compile(r"^\s*(?:Yandex(?: Browser)?\s+)?(\d+)\.(\d+)(?:\.\d+){0,2}\s*$", re.I)
_RELEASE_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)-stable$")
_WIN64_ASSET = re.compile(r"^yandexdriver-(\d+)\.(\d+)\.(\d+)\.(\d+)-win64\.zip$")
_SHA256 = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
_MAX_JSON_BYTES = 12 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 120 * 1024 * 1024
_MAX_EXE_BYTES = 120 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 30
_CACHE_LOCK = Lock()


class YandexDriverError(RuntimeError):
    """The matching driver could not be found or installed."""


def _branch(version: str) -> str:
    match = _BROWSER_VERSION.fullmatch(version)
    if match is None:
        raise YandexDriverError(
            "Не удалось определить версию Яндекс.Браузера. Ожидается номер вида 26.8.1.1019."
        )
    return f"{int(match[1])}.{int(match[2])}"


def _request(url: str, limit: int, *, github_api: bool = False) -> bytes:
    headers = {"User-Agent": "WebParser-YandexDriver/1.0"}
    if github_api:
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            chunks = bytearray()
            while True:
                part = response.read(min(1024 * 1024, limit + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
                if len(chunks) > limit:
                    raise YandexDriverError("Файл драйвера или ответ GitHub превышает допустимый размер.")
            return bytes(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YandexDriverError(f"Не удалось получить данные с GitHub: {exc}") from exc


def _release_assets(branch: str) -> list[tuple[tuple[int, ...], dict]]:
    candidates: list[tuple[tuple[int, ...], dict]] = []
    for page in range(1, 21):
        url = f"{_RELEASES_URL}?per_page=100&page={page}"
        try:
            releases = json.loads(_request(url, _MAX_JSON_BYTES, github_api=True))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise YandexDriverError("GitHub вернул некорректный список релизов YandexDriver.") from exc
        if not isinstance(releases, list):
            raise YandexDriverError("GitHub вернул неожиданный формат списка релизов YandexDriver.")
        for release in releases:
            if not isinstance(release, dict):
                continue
            tag = release.get("tag_name")
            match = _RELEASE_TAG.fullmatch(tag) if isinstance(tag, str) else None
            if match is None or f"{int(match[1])}.{int(match[2])}" != branch:
                continue
            assets = release.get("assets")
            if not isinstance(assets, list):
                continue
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = asset.get("name")
                asset_match = _WIN64_ASSET.fullmatch(name) if isinstance(name, str) else None
                if asset_match is None or f"{int(asset_match[1])}.{int(asset_match[2])}" != branch:
                    continue
                key = tuple(int(part) for part in asset_match.groups())
                candidates.append((key, asset))
        if len(releases) < 100:
            return candidates
    raise YandexDriverError("Список релизов YandexDriver слишком длинный; подбор остановлен.")


def _download_url(asset: dict) -> str:
    url = asset.get("browser_download_url")
    if not isinstance(url, str):
        raise YandexDriverError("В релизе YandexDriver отсутствует ссылка на Windows-архив.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not parsed.path.startswith(
        "/yandex/YandexDriver/releases/download/"
    ):
        raise YandexDriverError("Ссылка на Windows-архив YandexDriver имеет неожиданный адрес.")
    return url


def _driver_from_zip(archive: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            matches = []
            for member in zipped.infolist():
                path = PurePosixPath(member.filename)
                if member.is_dir() or path.name.lower() != "yandexdriver.exe":
                    continue
                if (member.filename.startswith(("/", "\\")) or "\\" in member.filename
                        or any(piece in ("", ".", "..") for piece in member.filename.split("/"))):
                    raise YandexDriverError("Архив YandexDriver содержит небезопасный путь к драйверу.")
                if not 0 < member.file_size <= _MAX_EXE_BYTES:
                    raise YandexDriverError("Исполняемый файл YandexDriver имеет недопустимый размер.")
                matches.append(member)
            if len(matches) != 1:
                raise YandexDriverError("В архиве YandexDriver не найден единственный yandexdriver.exe.")
            with zipped.open(matches[0]) as source:
                driver = source.read(_MAX_EXE_BYTES + 1)
            if not driver or len(driver) > _MAX_EXE_BYTES:
                raise YandexDriverError("Исполняемый файл YandexDriver имеет недопустимый размер.")
            return driver
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, YandexDriverError):
            raise
        raise YandexDriverError("Скачанный архив YandexDriver повреждён.") from exc


def _cached_driver(folder: Path, branch: str) -> Path | None:
    exe = folder / "yandexdriver.exe"
    manifest = folder / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("branch") != branch or not isinstance(data.get("exe_sha256"), str):
            return None
        if hashlib.sha256(exe.read_bytes()).hexdigest() != data["exe_sha256"]:
            return None
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return None
    return exe


def ensure_yandex_driver(browser_version: str, cache_dir: Path | None = None) -> Path:
    """Return a cached matching Windows x64 YandexDriver, downloading it if needed.

    Yandex publishes driver releases by browser branch (for example, 26.8.x).
    The official GitHub release assets are the only download source.
    """
    # Four site tabs can request the same driver at once. Serialize cache
    # updates so one tab cannot replace an executable another tab is starting.
    with _CACHE_LOCK:
        return _ensure_yandex_driver(browser_version, cache_dir)


def _ensure_yandex_driver(browser_version: str, cache_dir: Path | None) -> Path:
    branch = _branch(browser_version)
    if cache_dir is None:
        local_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_dir = local_data / "WebParser" / "drivers" / "yandex"
    folder = Path(cache_dir) / branch
    cached = _cached_driver(folder, branch)
    if cached is not None:
        return cached

    candidates = _release_assets(branch)
    if not candidates:
        raise YandexDriverError(
            f"Для Яндекс.Браузера {branch}.x нет официального YandexDriver для Windows x64. "
            "Проверьте релизы https://github.com/yandex/YandexDriver/releases."
        )
    _, asset = max(candidates, key=lambda candidate: candidate[0])
    name = asset["name"]
    url = _download_url(asset)
    size = asset.get("size")
    if isinstance(size, int) and (size <= 0 or size > _MAX_ARCHIVE_BYTES):
        raise YandexDriverError("Windows-архив YandexDriver имеет недопустимый размер.")
    archive = _request(url, _MAX_ARCHIVE_BYTES)
    digest = asset.get("digest")
    if digest is not None:
        digest_match = _SHA256.fullmatch(digest) if isinstance(digest, str) else None
        if digest_match is None or hashlib.sha256(archive).hexdigest() != digest_match[1].lower():
            raise YandexDriverError("Контрольная сумма Windows-архива YandexDriver не совпадает.")
    driver = _driver_from_zip(archive)
    exe_hash = hashlib.sha256(driver).hexdigest()

    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".yandexdriver-", suffix=".exe", dir=folder, delete=False) as temp:
            temp.write(driver)
            temp_exe = Path(temp.name)
        with tempfile.NamedTemporaryFile(prefix=".manifest-", suffix=".json", dir=folder, delete=False) as temp:
            temp.write(json.dumps({"branch": branch, "asset": name, "exe_sha256": exe_hash}).encode("utf-8"))
            temp_manifest = Path(temp.name)
        os.replace(temp_exe, folder / "yandexdriver.exe")
        os.replace(temp_manifest, folder / "manifest.json")
    except OSError as exc:
        raise YandexDriverError(f"Не удалось сохранить YandexDriver в локальном кэше: {exc}") from exc
    finally:
        for temporary in (locals().get("temp_exe"), locals().get("temp_manifest")):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return folder / "yandexdriver.exe"
