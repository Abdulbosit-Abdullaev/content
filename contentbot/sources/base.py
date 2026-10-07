"""What every video source returns, plus forgiving parsing helpers for scraped data."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from contentbot.models import Candidate, Keyword


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
