"""Shared data types used across the bot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Status(StrEnum):
    FOUND = "found"
    FILTERED_OUT = "filtered_out"
    SCORED = "scored"
    SELECTED = "selected"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    POSTED = "posted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class AudioMode(StrEnum):
    ORIGINAL = "original"
    MUTE = "mute"
    MUSIC = "music"


CATEGORIES: tuple[str, ...] = (
    "mechanism",
    "foam",
    "fabric",
    "leather",
    "legs",
    "tools",
    "fittings",
    "upholstery_work",
    "finished_furniture",
    "other",
)

PLATFORMS: tuple[str, ...] = ("youtube", "tiktok", "instagram", "pinterest")


@dataclass
class Candidate:
    """A video found by a source, before it is stored."""

    platform: str
    platform_id: str
    url: str
    media_url: str | None = None
    thumbnail_url: str | None = None
    title: str = ""
    description: str = ""
    author: str = ""
    duration_s: float | None = None
    views: int | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class Score:
    """Claude's verdict for one candidate."""

    score: int
    category: str
    competitor_branding: bool
    reason: str


@dataclass(frozen=True)
class Keyword:
    id: int
    text: str
    language: str
    kind: str  # "query" or "hashtag"


def utc_now() -> datetime:
    return datetime.now(UTC)
