from datetime import datetime, timezone
from typing import Any, Callable, Optional

CURSOR_SEPARATOR = "|"


def encode_cursor(created_at: datetime, entity_id: str) -> str:
    return f"{created_at.isoformat()}{CURSOR_SEPARATOR}{entity_id}"


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    timestamp, separator, entity_id = cursor.rpartition(CURSOR_SEPARATOR)
    if not separator or not timestamp or not entity_id:
        raise ValueError("INVALID_CURSOR")
    try:
        created_at = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError("INVALID_CURSOR") from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at, entity_id


def page_items(
    values: list[Any],
    limit: int,
    timestamp_of: Callable[[Any], Optional[datetime]],
) -> tuple[list[Any], Optional[str]]:
    """Trim a limit+1 repository result and build the next stable cursor."""
    page = values[:limit]
    if len(values) <= limit or not page:
        return page, None
    last = page[-1]
    created_at = timestamp_of(last) or datetime.min.replace(tzinfo=timezone.utc)
    return page, encode_cursor(created_at, last.id)
