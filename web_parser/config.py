"""Import/export of site settings with Windows user-bound secrets."""

from __future__ import annotations

import base64
import binascii
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


VERSION = 2
PROXY_SLOTS = 3
SITES = ("sail-master.ru", "fishingline.ru", "petrokanat.ru", "setepolotna.ru")
BROWSERS = ("Яндекс Браузер", "Google Chrome", "Microsoft Edge", "Mozilla Firefox")
INTERVAL_UNITS = ("hours", "days", "weeks", "months")
_SECRET_MARKER = b"web-parser-config-v1:\0"
_TIME_PATTERN = re.compile(r"\d{2}:\d{2}\Z")
_DPAPI_NO_UI = 0x01


class ConfigError(ValueError):
    """A configuration cannot be saved or loaded safely."""


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    sites: dict[str, dict]
    proxy_pool: tuple[dict[str, str], ...]
    email: dict[str, str | int | bool]

    @property
    def proxy(self) -> dict[str, str | bool]:
        """Expose the first active proxy to callers using the old single-proxy API."""
        first = next((slot for slot in self.proxy_pool if slot["server"]), None)
        return {"enabled": first is not None, **(first or {
            "server": "", "username": "", "password": "",
        })}


def default_email() -> dict[str, str | int | bool]:
    return {
        "enabled": False, "host": "", "port": 465, "security": "ssl",
        "username": "", "password": "", "sender": "", "recipient": "", "recipient2": "",
    }


def validate_email(value: object) -> dict[str, str | int | bool]:
    fields = _require_fields(
        value, {"enabled", "host", "port", "security", "username", "password", "sender", "recipient"},
        "Почта", optional=("recipient2",),
    )
    if type(fields["enabled"]) is not bool:
        raise ConfigError("Почта: признак включения должен быть логическим.")
    host = _string(fields["host"], "Почта, SMTP-сервер").strip()
    port = _integer(fields["port"], 1, 65535, "Почта, порт SMTP")
    security = _string(fields["security"], "Почта, защита соединения")
    username = _string(fields["username"], "Почта, логин")
    password = _string(fields["password"], "Почта, пароль")
    sender = _string(fields["sender"], "Почта, адрес отправителя").strip()
    recipient = _string(fields["recipient"], "Почта, адрес администратора").strip()
    recipient2 = _string(fields.get("recipient2", ""), "Почта, второй адрес").strip()
    if security not in {"ssl", "starttls"}:
        raise ConfigError("Почта: выберите SSL/TLS или STARTTLS.")
    if fields["enabled"]:
        if not host or any(char.isspace() or char in "/\\:@" for char in host):
            raise ConfigError("Почта: укажите адрес SMTP-сервера без протокола и порта.")
        if bool(username) != bool(password):
            raise ConfigError("Почта: укажите логин и пароль вместе либо оставьте оба поля пустыми.")
        if not all(re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", address) for address in (sender, recipient)):
            raise ConfigError("Почта: укажите корректные адреса отправителя и администратора.")
        if recipient2 and not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", recipient2):
            raise ConfigError("Почта: укажите корректный второй адрес получателя.")
        if recipient2 and recipient2.casefold() == recipient.casefold():
            raise ConfigError("Почта: адреса получателей должны различаться.")
    return {
        "enabled": fields["enabled"], "host": host, "port": port, "security": security,
        "username": username, "password": password, "sender": sender,
        "recipient": recipient, "recipient2": recipient2,
    }


def default_proxy() -> dict[str, str | bool]:
    return {"enabled": False, "server": "", "username": "", "password": ""}


def default_proxy_pool() -> tuple[dict[str, str], ...]:
    return tuple(
        {"server": "", "username": "", "password": ""} for _ in range(PROXY_SLOTS)
    )


def validate_proxy(value: object) -> dict[str, str | bool]:
    """Validate one app-wide browser proxy, without resolving its country."""
    fields = _require_fields(value, {"enabled", "server", "username", "password"}, "Прокси")
    enabled = fields["enabled"]
    if type(enabled) is not bool:
        raise ConfigError("Прокси: признак включения должен быть логическим.")
    server = _string(fields["server"], "Прокси, адрес").strip()
    username = _string(fields["username"], "Прокси, логин")
    password = _string(fields["password"], "Прокси, пароль")
    if enabled:
        try:
            parsed = urlsplit(server)
            valid = (
                parsed.scheme in {"http", "https", "socks5"}
                and bool(parsed.hostname) and parsed.port is not None
                and 1 <= parsed.port <= 65535
                and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
                and not any(char.isspace() for char in server)
            )
        except ValueError:
            valid = False
            parsed = None
        if not valid:
            raise ConfigError(
                "Прокси: укажите адрес и порт с протоколом HTTP, HTTPS или SOCKS5; "
                "логин и пароль вводите в отдельных полях."
            )
        if bool(username) != bool(password):
            raise ConfigError("Прокси: укажите и логин, и пароль либо оставьте оба поля пустыми.")
        if parsed is not None and parsed.scheme == "socks5" and username:
            if len(username.encode("utf-8")) > 255 or len(password.encode("utf-8")) > 255:
                raise ConfigError("Прокси: логин и пароль SOCKS5 должны быть не длиннее 255 байт.")
    return {"enabled": enabled, "server": server, "username": username, "password": password}


def validate_proxy_pool(value: object) -> tuple[dict[str, str], ...]:
    """Validate three optional slots; credentials alone are an incomplete slot."""
    if not isinstance(value, (list, tuple)) or len(value) > PROXY_SLOTS:
        raise ConfigError(f"Прокси: допускается не более {PROXY_SLOTS} слотов.")
    slots = []
    for index, value_slot in enumerate(value, 1):
        fields = _require_fields(
            value_slot, {"server", "username", "password"}, f"Прокси №{index}",
        )
        server = _string(fields["server"], f"Прокси №{index}, адрес").strip()
        username = _string(fields["username"], f"Прокси №{index}, логин")
        password = _string(fields["password"], f"Прокси №{index}, пароль")
        if server or username or password:
            if not server:
                raise ConfigError(f"Прокси №{index}: укажите адрес или очистите логин и пароль.")
            try:
                checked = validate_proxy({
                    "enabled": True, "server": server,
                    "username": username, "password": password,
                })
            except ConfigError as error:
                raise ConfigError(
                    f"Прокси №{index}: {str(error).removeprefix('Прокси: ')}"
                ) from error
            server = str(checked["server"])
        slots.append({"server": server, "username": username, "password": password})
    slots.extend(default_proxy_pool()[len(slots):])
    return tuple(slots)


def playwright_proxy(value: dict[str, str | bool]) -> dict[str, str] | None:
    """Return validated proxy settings for the browser session."""
    checked = validate_proxy(value)
    if not checked["enabled"]:
        return None
    options = {"server": str(checked["server"])}
    if checked["username"]:
        options["username"] = str(checked["username"])
        options["password"] = str(checked["password"])
    return options


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _require_fields(
    value: object, names: set[str], location: str, *, optional: tuple[str, ...] = (),
) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{location}: ожидался объект JSON.")
    fields = set(value)
    if not names <= fields or fields - names - set(optional):
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


def _time(value: object, location: str) -> str:
    checked = _string(value, location)
    if not _TIME_PATTERN.fullmatch(checked) or int(checked[:2]) > 23 or int(checked[3:]) > 59:
        raise ConfigError(f"{location} должно быть в формате ЧЧ:ММ.")
    return checked


def _validate_site(value: object, site: str) -> dict:
    location = f"Настройки {site}"
    settings = _require_fields(
        value,
        {"site", "source_url", "token", "browser", "pause_seconds", "ftp", "schedule"},
        location,
        optional=(
            "source_schedule_enabled", "source_schedule_time",
            "source_schedule_interval", "source_schedule_unit",
        ),
    )
    if settings["site"] != site:
        raise ConfigError(f"{location}: имя сайта не совпадает с вкладкой.")
    source_url = _string(settings["source_url"], f"{location}, URL списка товаров")
    token = _string(settings["token"], f"{location}, токен")
    source_schedule_enabled = settings.get("source_schedule_enabled", False)
    if type(source_schedule_enabled) is not bool:
        raise ConfigError(f"{location}: признак расписания источника должен быть логическим.")
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
    start_time = _time(schedule["start_time"], f"{location}: время запуска")
    source_schedule_time = _time(
        settings.get("source_schedule_time", start_time), f"{location}: время загрузки JSON",
    )
    interval = _integer(schedule["interval"], 1, 999, f"{location}, интервал")
    unit = _string(schedule["unit"], f"{location}, единица интервала")
    if unit not in INTERVAL_UNITS:
        raise ConfigError(f"{location}: неизвестная единица интервала.")
    source_schedule_interval = _integer(
        settings.get("source_schedule_interval", interval), 1, 999,
        f"{location}, интервал загрузки JSON",
    )
    source_schedule_unit = _string(
        settings.get("source_schedule_unit", unit), f"{location}, единица интервала загрузки JSON",
    )
    if source_schedule_unit not in INTERVAL_UNITS:
        raise ConfigError(f"{location}: неизвестная единица интервала загрузки JSON.")

    return {
        "site": site,
        "source_url": source_url,
        "token": token,
        "source_schedule_enabled": source_schedule_enabled,
        "source_schedule_time": source_schedule_time,
        "source_schedule_interval": source_schedule_interval,
        "source_schedule_unit": source_schedule_unit,
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

    input_buffer = ctypes.create_string_buffer(data, len(data))
    input_blob = _DataBlob(len(data), ctypes.addressof(input_buffer))
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


def save(
    path: str | Path, sites: dict[str, dict],
    proxy: dict[str, str | bool] | list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
    email: dict[str, str | int | bool] | None = None,
) -> None:
    """Atomically export all tabs and the shared proxy pool."""
    checked = _validate_sites(sites)
    if isinstance(proxy, dict):
        old_proxy = validate_proxy(proxy)
        pool = list(default_proxy_pool())
        if old_proxy["enabled"]:
            pool[0] = {key: str(old_proxy[key]) for key in ("server", "username", "password")}
        checked_pool = validate_proxy_pool(pool)
    else:
        checked_pool = validate_proxy_pool(proxy if proxy is not None else default_proxy_pool())
    checked_email = validate_email(email) if email is not None else None
    for settings in checked.values():
        settings["token"] = _encrypt(settings["token"])
        settings["ftp"]["password"] = _encrypt(settings["ftp"]["password"])
    try:
        document: dict[str, object] = {
            "version": VERSION, "sites": checked,
            "proxies": [
                {**slot, "password": _encrypt(slot["password"])} for slot in checked_pool
            ],
        }
        if checked_email is not None:
            document["email"] = {
                **checked_email, "password": _encrypt(str(checked_email["password"])),
            }
        serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
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


def load_document(path: str | Path) -> LoadedConfig:
    """Read and fully validate sites and the shared proxy before applying either."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"Не удалось прочитать файл конфигурации: {error}") from error
    document = _require_fields(
        document, {"version", "sites"}, "Конфигурация", optional=("proxy", "proxies", "email"),
    )
    version = document["version"]
    if type(version) is not int or version not in (1, VERSION):
        raise ConfigError("Неподдерживаемая версия файла конфигурации.")
    if (version == 1 and "proxies" in document) or (
        version == VERSION and ("proxy" in document or "proxies" not in document)
    ):
        raise ConfigError("Конфигурация: неверный набор полей прокси.")
    encrypted_sites = _require_fields(document["sites"], set(SITES), "Сайты")
    plain_sites = {}
    for site in SITES:
        encrypted = _require_fields(
            encrypted_sites[site],
            {"site", "source_url", "token", "browser", "pause_seconds", "ftp", "schedule"},
            f"Настройки {site}",
            optional=(
                "source_schedule_enabled", "source_schedule_time",
                "source_schedule_interval", "source_schedule_unit",
            ),
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
    pool = list(default_proxy_pool())
    if version == 1 and "proxy" in document:
        encrypted_proxy = _require_fields(
            document["proxy"], {"enabled", "server", "username", "password"}, "Прокси",
        )
        old_proxy = validate_proxy({
            **encrypted_proxy,
            "password": _decrypt(encrypted_proxy["password"], "Прокси, пароль"),
        })
        if old_proxy["enabled"]:
            pool[0] = {key: str(old_proxy[key]) for key in ("server", "username", "password")}
    elif version == VERSION:
        encrypted_pool = document["proxies"]
        if not isinstance(encrypted_pool, list) or len(encrypted_pool) > PROXY_SLOTS:
            raise ConfigError(f"Прокси: допускается не более {PROXY_SLOTS} слотов.")
        for index, encrypted_slot in enumerate(encrypted_pool):
            encrypted_slot = _require_fields(
                encrypted_slot, {"server", "username", "password"}, f"Прокси №{index + 1}",
            )
            pool[index] = {
                **encrypted_slot,
                "password": _decrypt(encrypted_slot["password"], f"Прокси №{index + 1}, пароль"),
            }
    email = default_email()
    if "email" in document:
        encrypted_email = _require_fields(
            document["email"],
            {"enabled", "host", "port", "security", "username", "password", "sender", "recipient"},
            "Почта", optional=("recipient2",),
        )
        email = validate_email({
            **encrypted_email,
            "password": _decrypt(encrypted_email["password"], "Почта, пароль"),
        })
    return LoadedConfig(_validate_sites(plain_sites), validate_proxy_pool(pool), email)


def load(path: str | Path) -> dict[str, dict]:
    """Read site settings, retaining the original sites-only API."""
    return load_document(path).sites
