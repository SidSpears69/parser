"""Report successful fallback extraction without interrupting a parser run."""

from collections.abc import Callable, Iterable


def browser_fallback_fields(raw_cards: object) -> set[str]:
    """Collect known fallback markers from validated browser card payloads."""
    result: set[str] = set()
    if not isinstance(raw_cards, list):
        return result
    for card in raw_cards:
        if not isinstance(card, dict):
            continue
        fields = card.get("_layout_fallbacks")
        if isinstance(fields, list):
            result.update(field for field in fields if isinstance(field, str))
    return result


def report_layout_fallback(
    notify: Callable[[str], None] | None, fields: Iterable[str],
) -> None:
    changed = sorted(set(fields))
    if notify is not None and changed:
        notify(
            "Изменилась структура выдачи; используются запасные селекторы или поля JSON для "
            + ", ".join(changed) + ". Проверьте вёрстку или формат JSON."
        )
