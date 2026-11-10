"""Offline smoke check for a packaged desktop build."""

from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from web_parser.app import create_application
from web_parser.browser_access import BrowserAccessError, BrowserPageAccess
from web_parser.browser_discovery import BrowserInstallation, discover_browsers
from web_parser.config import BROWSERS
from web_parser.paths import ARTIFACT_ROOT
from web_parser.ui import MainWindow


def run_self_test(browser_name: str | None = None) -> int:
    """Check Qt, Playwright driver, and one visible browser without network access."""
    report_path = ARTIFACT_ROOT / "self-test.txt"
    lines: list[str] = []
    try:
        application = create_application(["WebParser.exe"])
        window = MainWindow()
        window.show()
        application.processEvents()
        lines.append("Qt window: OK")
        available = discover_browsers()
        with sync_playwright() as playwright:
            lines.append("Playwright driver: OK")
            selected = browser_name or next((name for name in BROWSERS if name in available), None)
            if selected is None:
                raise RuntimeError("Не найден установленный браузер для проверки.")
            installation = available.get(selected)
            if installation is None:
                raise RuntimeError(f"Браузер не найден: {selected}.")
            if selected == "Mozilla Firefox":
                installation = BrowserInstallation(
                    selected, Path(playwright.firefox.executable_path),
                )
            with TemporaryDirectory(prefix="web-parser-self-test-") as folder:
                with BrowserPageAccess(
                    installation, 0, headless=False, profile_dir=Path(folder),
                    playwright=playwright,
                ):
                    application.processEvents()
            lines.append(f"Browser launch: OK ({selected})")
        window.close()
        application.processEvents()
        lines.append("SELF TEST PASSED")
        result = 0
    except (ImportError, OSError, RuntimeError, PlaywrightError, BrowserAccessError) as error:
        lines.append(f"SELF TEST FAILED: {error}")
        result = 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
