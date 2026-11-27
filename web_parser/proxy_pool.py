"""Keep the last working proxy for each marketplace across walks."""

from dataclasses import dataclass, field
from threading import Lock
from typing import Callable

from web_parser.config import validate_proxy_pool


MARKETPLACES = ("Ozon", "Wildberries", "Яндекс Маркет")


@dataclass(frozen=True, slots=True)
class WalkProxyRoutes:
    proxies: dict[int, dict[str, str] | None] = field(repr=False)
    candidates: dict[str, tuple[int, ...]]
    remember: Callable[[str, int], None] = field(repr=False)


class ProxyPool:
    def __init__(self) -> None:
        self._lock = Lock()
        self._slots: tuple[dict[str, str], ...] | None = None
        self._working: dict[str, int] = {}

    def routes_for_walk(self, slots: object) -> WalkProxyRoutes:
        checked = validate_proxy_pool(slots)
        proxies: dict[int, dict[str, str] | None] = {0: None}
        for slot_number, slot in enumerate(checked, 1):
            if slot["server"]:
                proxies[slot_number] = {key: value for key, value in slot.items() if value}
        order = tuple(index for index in proxies if index) + (0,)

        with self._lock:
            if checked != self._slots:
                self._slots = checked
                self._working.clear()
            snapshot = self._slots
            candidates = {}
            for marketplace_name in MARKETPLACES:
                preferred = self._working.get(marketplace_name, order[0])
                candidates[marketplace_name] = (
                    preferred, *(index for index in order if index != preferred),
                )

        def remember(marketplace: str, index: int) -> None:
            with self._lock:
                if self._slots is snapshot:
                    self._working[marketplace] = index

        return WalkProxyRoutes(proxies, candidates, remember)
