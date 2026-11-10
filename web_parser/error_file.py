"""Persistent, bounded error logs for the four source sites."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from web_parser.config import SITES
from web_parser.paths import ARTIFACT_ROOT


DEFAULT_LOG_ROOT = ARTIFACT_ROOT / "logs"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


class ErrorFile:
    """Append UTF-8 lines and retain a few older files when the log fills up."""

    def __init__(
        self, site: str, root: Path | None = None, *,
        max_bytes: int = MAX_LOG_BYTES, backups: int = LOG_BACKUPS,
    ) -> None:
        if site not in SITES:
            raise ValueError("Неизвестный сайт для журнала ошибок.")
        if max_bytes < 1 or backups < 0:
            raise ValueError("Недопустимый размер журнала ошибок.")
        self.path = (Path(root) if root is not None else DEFAULT_LOG_ROOT) / f"{site}.log"
        self.max_bytes = max_bytes
        self.backups = backups

    def append(self, line: str) -> None:
        payload = (line + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(payload) > self.max_bytes:
            self._rotate()
        with self.path.open("ab") as stream:
            stream.write(payload)

    def _rotate(self) -> None:
        if self.backups == 0:
            self.path.unlink(missing_ok=True)
            return
        for number in range(self.backups, 1, -1):
            older = self.path.with_name(f"{self.path.name}.{number - 1}")
            newer = self.path.with_name(f"{self.path.name}.{number}")
            if older.exists():
                older.replace(newer)
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def recent(self, limit: int = 2000) -> tuple[str, ...]:
        if not self.path.exists():
            return ()
        with self.path.open("r", encoding="utf-8", errors="replace") as stream:
            return tuple(line.rstrip("\r\n") for line in deque(stream, maxlen=limit))
