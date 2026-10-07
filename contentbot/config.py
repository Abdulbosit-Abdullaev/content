"""Load settings.yaml (owner-editable) and .env (secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """settings.yaml or .env is missing something or has a bad value."""


@dataclass(frozen=True)
class ApifyActor:
    actor_id: str
    price_per_1000: float
    max_results: int


@dataclass(frozen=True)
class Settings:
    timezone: ZoneInfo
    search_time: time
    post_times: tuple[time, ...]
    candidates_per_day: int
    max_per_category: int
    review_expiry_hours: int
    min_duration_s: float
    max_duration_s: float
    min_views: dict[str, int]
    keywords_per_run: int
    youtube_per_keyword: int
    ai_check_limit: int
    min_ai_score: int
    ai_model: str
    apify_monthly_budget_usd: float
    apify_actors: dict[str, ApifyActor]
    ytdlp_cookies_file: str | None
    ffmpeg_path: str
    ffprobe_path: str
    footer: str
    caption_examples: tuple[str, ...]
    seed_keywords: dict[str, list[str]]
    data_dir: Path
    music_dir: Path


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str
    review_chat_id: int
    channel_id: str
    anthropic_api_key: str
    youtube_api_key: str
    apify_token: str


REQUIRED_KEYS = (
    "timezone",
    "search_time",
    "post_times",
    "candidates_per_day",
    "max_per_category",
    "review_expiry_hours",
    "duration",
    "min_views",
    "keywords_per_run",
    "youtube_per_keyword",
    "ai_check_limit",
    "min_ai_score",
    "ai",
    "apify",
    "footer",
    "caption_examples",
    "seed_keywords",
)

SECRET_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "REVIEW_CHAT_ID",
    "CHANNEL_ID",
    "ANTHROPIC_API_KEY",
    "YOUTUBE_API_KEY",
    "APIFY_TOKEN",
)


def parse_hhmm(value: Any) -> time:
    if not isinstance(value, str):
        raise ConfigError(f"Bad time {value!r}: write times in quotes, like \"07:00\"")
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ConfigError(f"Bad time {value!r}: expected HH:MM") from exc


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"settings.yaml is missing: {', '.join(missing)}")
    base = path.resolve().parent
    try:
        actors = {
            str(name): ApifyActor(
                actor_id=str(actor["actor_id"]),
                price_per_1000=float(actor["price_per_1000"]),
                max_results=int(actor["max_results"]),
            )
            for name, actor in raw["apify"]["actors"].items()
        }
        return Settings(
            timezone=ZoneInfo(str(raw["timezone"])),
            search_time=parse_hhmm(raw["search_time"]),
            post_times=tuple(sorted(parse_hhmm(t) for t in raw["post_times"])),
            candidates_per_day=int(raw["candidates_per_day"]),
            max_per_category=int(raw["max_per_category"]),
            review_expiry_hours=int(raw["review_expiry_hours"]),
            min_duration_s=float(raw["duration"]["min_s"]),
            max_duration_s=float(raw["duration"]["max_s"]),
            min_views={str(k): int(v) for k, v in raw["min_views"].items()},
            keywords_per_run=int(raw["keywords_per_run"]),
            youtube_per_keyword=int(raw["youtube_per_keyword"]),
            ai_check_limit=int(raw["ai_check_limit"]),
            min_ai_score=int(raw["min_ai_score"]),
            ai_model=str(raw["ai"]["model"]),
            apify_monthly_budget_usd=float(raw["apify"]["monthly_budget_usd"]),
            apify_actors=actors,
            ytdlp_cookies_file=raw.get("ytdlp_cookies_file") or None,
            ffmpeg_path=str(raw.get("ffmpeg_path") or "ffmpeg"),
            ffprobe_path=str(raw.get("ffprobe_path") or "ffprobe"),
            footer=str(raw["footer"]).strip(),
            caption_examples=tuple(str(x).strip() for x in raw["caption_examples"]),
            seed_keywords={str(k): [str(x) for x in v] for k, v in raw["seed_keywords"].items()},
            data_dir=(base / str(raw.get("data_dir", "data"))).resolve(),
            music_dir=(base / str(raw.get("music_dir", "music"))).resolve(),
        )
    except KeyError as exc:
        raise ConfigError(f"settings.yaml is missing key: {exc}") from exc
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown timezone {raw['timezone']!r}") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"settings.yaml has a bad value: {exc}") from exc


def load_secrets(env_file: str | Path | None = ".env") -> Secrets:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=False)
    missing = [var for var in SECRET_VARS if not os.environ.get(var)]
    if missing:
        raise ConfigError("Missing in .env: " + ", ".join(missing))
    try:
        review_chat_id = int(os.environ["REVIEW_CHAT_ID"])
    except ValueError as exc:
        raise ConfigError("REVIEW_CHAT_ID must be a number like -1001234567890") from exc
    return Secrets(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        review_chat_id=review_chat_id,
        channel_id=os.environ["CHANNEL_ID"],
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        youtube_api_key=os.environ["YOUTUBE_API_KEY"],
        apify_token=os.environ["APIFY_TOKEN"],
    )
