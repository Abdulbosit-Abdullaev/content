"""What every video source returns, plus forgiving parsing helpers for scraped data."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from contentbot.models import Candidate, Keyword

log = logging.getLogger(__name__)


@dataclass
class SourceResult:
    candidates: list[Candidate] = field(default_factory=list)
    cost_usd: float = 0.0


class Source(Protocol):
    name: str
    timeout_s: float
    is_paid: bool

    async def search(self, keywords: list[Keyword]) -> SourceResult: ...


def as_query(text: str) -> str:
    return text.strip().lstrip("#").strip()


def as_hashtag(text: str) -> str:
    return text.strip().lstrip("#").replace(" ", "")


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def to_url(value: Any) -> str | None:
    """A web link as a string, or None for anything else (lists, objects, other schemes)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.startswith(("http://", "https://")) else None


def parse_all(parser: Callable[[Any], Candidate | None], items: Iterable[Any]) -> list[Candidate]:
    """Parse every item; one malformed item is skipped instead of sinking the whole source."""
    candidates: list[Candidate] = []
    for item in items:
        try:
            candidate = parser(item)
        except Exception as exc:  # scraped data can have any shape
            log.info("Skipping malformed item: %r", exc)
            continue
        if candidate:
            candidates.append(candidate)
    return candidates


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
