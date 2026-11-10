"""Readiness checks for an interactive Windows Server parser installation."""

from __future__ import annotations

import argparse
import ctypes
from importlib import metadata
from pathlib import Path
import platform
import ssl
import struct
import sys
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_LABELS = {"Яндекс Браузер": "Yandex Browser"}


def _result(level: str, label: str, detail: str) -> bool:
    print(f"{level}: {label}: {detail}")
    return level != "FAIL"


def _check_dependencies() -> bool:
    problems = []
    installed = []
    for raw_line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, expected = line.partition("==")
        if not separator or not name or not expected:
            problems.append(f"unsupported requirement: {line}")
            continue
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{name} missing")
            continue
        if actual != expected:
            problems.append(f"{name} {actual}, expected {expected}")
        else:
            installed.append(f"{name} {actual}")
    return _result("FAIL" if problems else "OK", "Python packages", "; ".join(problems or installed))


def _check_tls(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return _result("FAIL", "TLS", "invalid HTTPS URL")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return _result("FAIL", "TLS", "provide an HTTPS URL without credentials")
    if parsed.query or parsed.fragment:
        return _result("FAIL", "TLS", "provide a URL without query parameters or fragment")
    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        return _result("FAIL", "TLS", "certificate or hostname verification is disabled")
    target = parsed.hostname
    request = Request(url, headers={"User-Agent": "WebParser-Preflight/1.0"})
    try:
        with urlopen(request, timeout=15, context=context) as response:
            status = response.status
            final_url = response.geturl()
    except HTTPError as error:
        status = error.code  # A valid HTTPS response still proves certificate verification worked.
        final_url = error.geturl()
        error.close()
    except (URLError, ssl.SSLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return _result("FAIL", "TLS", f"{target}: {reason}")
    if urlsplit(final_url).scheme != "https":
        return _result("FAIL", "TLS", f"{target}: redirected to a non-HTTPS URL")
    return _result(
        "OK", "TLS", f"{target}: verified HTTPS, HTTP {status}, minimum {context.minimum_version.name}",
    )


def _check_browser(browser_name: str | None, launch: bool) -> bool:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    from web_parser.browser_access import BrowserAccessError, BrowserPageAccess
    from web_parser.browser_discovery import BrowserInstallation, discover_browsers
    from web_parser.config import BROWSERS

    available = discover_browsers()
    found = ", ".join(_BROWSER_LABELS.get(name, name) for name in available) or "none"
    _result("OK" if available else "WARN", "Installed browsers", found)
    try:
        with sync_playwright() as playwright:
            firefox_binary = Path(playwright.firefox.executable_path)
            firefox_ready = firefox_binary.is_file()
    except (PlaywrightError, OSError) as error:
        return _result("FAIL", "Playwright driver", str(error))
    _result("OK", "Playwright driver", "started successfully")
    _result(
        "OK" if firefox_ready else "WARN", "Playwright Firefox",
        str(firefox_binary) if firefox_ready else "not installed; run python -m playwright install firefox",
    )
    selected = browser_name or next((name for name in BROWSERS if name in available), None)
    if selected is None and firefox_ready:
        selected = "Mozilla Firefox"
    if selected is None:
        return _result("FAIL", "Browser", "no supported browser found")
    browser_label = _BROWSER_LABELS.get(selected, selected)
    if selected == "Mozilla Firefox":
        if not firefox_ready:
            return _result("FAIL", "Browser", "Playwright Firefox is not installed")
        installation = BrowserInstallation(selected, firefox_binary)
    else:
        installation = available.get(selected)
        if installation is None:
            return _result("FAIL", "Browser", f"{browser_label}: executable not found")
    if not launch:
        return _result("WARN", "Browser launch", f"{browser_label}: skipped")
    try:
        with TemporaryDirectory(prefix="web-parser-preflight-") as folder:
            with BrowserPageAccess(
                installation, 0, headless=False, profile_dir=Path(folder),
            ):
                pass
    except (BrowserAccessError, OSError) as error:
        return _result("FAIL", "Browser launch", f"{browser_label}: {error}")
    return _result("OK", "Browser launch", f"{browser_label}: opened and closed with a temporary profile")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Browser name as shown in the application")
    parser.add_argument("--tls-url", default="https://sail-master.ru/", help="HTTPS URL to verify")
    parser.add_argument("--skip-tls", action="store_true", help="Skip the network TLS probe")
    parser.add_argument("--skip-browser-launch", action="store_true", help="Skip the headed browser smoke test")
    options = parser.parse_args(argv)
    if options.browser:
        from web_parser.config import BROWSERS

        if options.browser not in BROWSERS:
            parser.error(f"unknown browser: {options.browser}")
    failures = 0

    if sys.platform != "win32":
        _result("FAIL", "OS", "Windows is required")
        return 1
    os_name, version, _, _ = platform.win32_ver()
    edition = platform.win32_edition()
    build = sys.getwindowsversion().build
    supported = (edition.startswith("Server") and build >= 17763) or build >= 22000
    os_detail = f"{os_name} {version}, {edition}, build {build}"
    if not supported:
        os_detail += "; Playwright documents Windows Server 2019+ or Windows 11+"
    _result("OK" if supported else "WARN", "OS", os_detail)
    python_ready = sys.version_info[:2] == (3, 14) and struct.calcsize("P") == 8
    failures += not _result(
        "OK" if python_ready else "FAIL", "Python",
        f"{sys.version.split()[0]}, {struct.calcsize('P') * 8}-bit, {sys.executable}",
    )
    if not python_ready:
        return 1
    try:
        ctypes.WinDLL("msvcp140_1.dll")
    except OSError:
        failures += not _result("FAIL", "VC++ runtime", "msvcp140_1.dll is unavailable")
    else:
        _result("OK", "VC++ runtime", "msvcp140_1.dll is available")
    failures += not _check_dependencies()
    try:
        from web_parser.app import create_application

        application = create_application(["web-parser-preflight"])
    except (ImportError, OSError, RuntimeError) as error:
        failures += not _result("FAIL", "Qt", str(error))
    else:
        _result("OK", "Qt", f"PySide6 platform {application.platformName()}")
        application.quit()
    if not options.skip_tls:
        failures += not _check_tls(options.tls_url)
    try:
        failures += not _check_browser(options.browser, not options.skip_browser_launch)
    except ImportError as error:
        failures += not _result("FAIL", "Browser", str(error))
    if failures:
        print(f"NOT READY: {failures} failed check(s)")
    elif options.skip_tls or options.skip_browser_launch:
        print("PARTIAL: requested checks passed; some checks were skipped")
    else:
        print("READY")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
