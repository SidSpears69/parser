"""Manual import/export of site settings with Windows user-bound secrets."""

from __future__ import annotations

import base64
import binascii
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import tempfile


VERSION = 1
SITES = ("sail-master.ru", "fishingline.ru", "petrokanat.ru", "commercial-fishing.ru")
BROWSERS = ("Яндекс Браузер", "Google Chrome", "Microsoft Edge", "Mozilla Firefox")
INTERVAL_UNITS = ("hours", "days", "weeks", "months")
_SECRET_MARKER = b"web-parser-config-v1:\0"
_TIME_PATTERN = re.compile(r"\d{2}:\d{2}\Z")
_DPAPI_NO_UI = 0x01


class ConfigError(ValueError):
    """A configuration cannot be saved or loaded safely."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _require_fields(value: object, names: set[str], location: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{location}: ожидался объект JSON.")
    if set(value) != names:
        raise ConfigError(f"{location}: неверный набор полей.")
    return value


def _string(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{location}: ожидалась строка.")
    return value


def _integer(value: object, minimum: int, maximum: int, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConfigError(f"{location}: допустимое число от {minimum} до {maximum}.")
    return value


def _validate_site(value: object, site: str) -> dict:
    location = f"Настройки {site}"
    settings = _require_fields(
        value,
        {"site", "source_url", "token", "browser", "pause_seconds", "ftp", "schedule"},
        location,
    )
    if settings["site"] != site:
        raise ConfigError(f"{location}: имя сайта не совпадает с вкладкой.")
    source_url = _string(settings["source_url"], f"{location}, URL списка товаров")
    token = _string(settings["token"], f"{location}, токен")
    browser = _string(settings["browser"], f"{location}, браузер")
    if browser not in BROWSERS:
        raise ConfigError(f"{location}: неизвестный браузер.")
    pause_seconds = _integer(settings["pause_seconds"], 1, 3600, f"{location}, пауза")

    ftp = _require_fields(
        settings["ftp"], {"host", "port", "username", "password", "remote_path"},
        f"{location}, FTP",
    )
    checked_ftp = {
        "host": _string(ftp["host"], f"{location}, адрес FTP"),
        "port": _integer(ftp["port"], 1, 65535, f"{location}, порт FTP"),
        "username": _string(ftp["username"], f"{location}, логин FTP"),
        "password": _string(ftp["password"], f"{location}, пароль FTP"),
        "remote_path": _string(ftp["remote_path"], f"{location}, каталог FTP"),
    }

    schedule = _require_fields(
        settings["schedule"], {"enabled", "start_time", "interval", "unit"},
        f"{location}, расписание",
    )
    enabled = schedule["enabled"]
    if type(enabled) is not bool:
        raise ConfigError(f"{location}: признак включения расписания должен быть логическим.")
    start_time = _string(schedule["start_time"], f"{location}, время запуска")
    if not _TIME_PATTERN.fullmatch(start_time) or int(start_time[:2]) > 23 or int(start_time[3:]) > 59:
        raise ConfigError(f"{location}: время запуска должно быть в формате ЧЧ:ММ.")
    interval = _integer(schedule["interval"], 1, 999, f"{location}, интервал")
    unit = _string(schedule["unit"], f"{location}, единица интервала")
    if unit not in INTERVAL_UNITS:
        raise ConfigError(f"{location}: неизвестная единица интервала.")

    return {
        "site": site,
        "source_url": source_url,
        "token": token,
        "browser": browser,
        "pause_seconds": pause_seconds,
        "ftp": checked_ftp,
        "schedule": {
            "enabled": enabled, "start_time": start_time, "interval": interval, "unit": unit,
        },
    }


def _validate_sites(sites: object) -> dict[str, dict]:
    checked = _require_fields(sites, set(SITES), "Сайты")
    return {site: _validate_site(checked[site], site) for site in SITES}


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise ConfigError("Шифрование конфигурации доступно только в Windows.")

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = (
        [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        if decrypt else
        [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    )
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    input_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = _DataBlob(len(data), input_buffer)
    output_blob = _DataBlob()
    if not function(ctypes.byref(input_blob), None, None, None, None,
                    _DPAPI_NO_UI, ctypes.byref(output_blob)):
        error = ctypes.get_last_error()
        action = "расшифровать" if decrypt else "зашифровать"
        raise ConfigError(f"Не удалось {action} секреты средствами Windows (код {error}).")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _encrypt(value: str) -> dict[str, str]:
    try:
        payload = _SECRET_MARKER + value.encode("utf-8")
    except UnicodeError as error:
        raise ConfigError("Секрет содержит недопустимые символы Unicode.") from error
    return {"dpapi": base64.b64encode(_dpapi(payload, decrypt=False)).decode("ascii")}


def _decrypt(value: object, location: str) -> str:
    envelope = _require_fields(value, {"dpapi"}, location)
    encoded = _string(envelope["dpapi"], location)
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ConfigError(f"{location}: повреждённое зашифрованное значение.") from error
    if not ciphertext:
        raise ConfigError(f"{location}: отсутствует зашифрованное значение.")
    plaintext = _dpapi(ciphertext, decrypt=True)
    if not plaintext.startswith(_SECRET_MARKER):
        raise ConfigError(f"{location}: неверный формат расшифрованного значения.")
    try:
        return plaintext[len(_SECRET_MARKER):].decode("utf-8")
    except UnicodeError as error:
        raise ConfigError(f"{location}: повреждённое расшифрованное значение.") from error


def save(path: str | Path, sites: dict[str, dict]) -> None:
    """Atomically export all four tabs; token and FTP password use DPAPI CurrentUser."""
    checked = _validate_sites(sites)
    for settings in checked.values():
        settings["token"] = _encrypt(settings["token"])
        settings["ftp"]["password"] = _encrypt(settings["ftp"]["password"])
    try:
        serialized = json.dumps({"version": VERSION, "sites": checked}, ensure_ascii=False, indent=2) + "\n"
        destination = Path(path)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"Не удалось сохранить файл конфигурации: {error}") from error


def load(path: str | Path) -> dict[str, dict]:
    """Read and fully validate a file before returning settings for any tab."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"Не удалось прочитать файл конфигурации: {error}") from error
    document = _require_fields(document, {"version", "sites"}, "Конфигурация")
    if type(document["version"]) is not int or document["version"] != VERSION:
        raise ConfigError("Неподдерживаемая версия файла конфигурации.")
    encrypted_sites = _require_fields(document["sites"], set(SITES), "Сайты")
    plain_sites = {}
    for site in SITES:
        encrypted = _require_fields(
            encrypted_sites[site],
            {"site", "source_url", "token", "browser", "pause_seconds", "ftp", "schedule"},
            f"Настройки {site}",
        )
        ftp = _require_fields(
            encrypted["ftp"], {"host", "port", "username", "password", "remote_path"},
            f"Настройки {site}, FTP",
        )
        plain_sites[site] = {
            **encrypted,
            "token": _decrypt(encrypted["token"], f"Настройки {site}, токен"),
            "ftp": {
                **ftp,
                "password": _decrypt(ftp["password"], f"Настройки {site}, пароль FTP"),
            },
        }
    return _validate_sites(plain_sites)
