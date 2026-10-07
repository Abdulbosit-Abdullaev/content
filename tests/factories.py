"""Test data builders shared by many test files."""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

from contentbot.config import Secrets, Settings, load_settings
from contentbot.db import Database, VideoRow
from contentbot.models import Candidate, Status

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOW = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)  # 08:00 in Tashkent


def make_candidate(**overrides) -> Candidate:
    values = dict(
        platform="youtube",
        platform_id="vid1",
        url="https://www.youtube.com/shorts/vid1",
        media_url=None,
        thumbnail_url="https://img.example/1.jpg",
        title="Sofa mechanism",
        description="#sofa #mechanism",
        author="maker",
        duration_s=30.0,
        views=5000,
        published_at=None,
    )
    values.update(overrides)
    return Candidate(**values)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    settings = load_settings(ROOT / "settings.yaml")
    return dataclasses.replace(
        settings, data_dir=tmp_path / "data", music_dir=tmp_path / "music", **overrides
    )


def make_secrets(**overrides) -> Secrets:
    values = dict(
        telegram_bot_token="123456:TESTTOKEN",
        review_chat_id=-100111,
        channel_id="@test_channel",
        anthropic_api_key="test-key",
        youtube_api_key="yt-key",
        apify_token="apify-token",
    )
    values.update(overrides)
    return Secrets(**values)


def make_video(db: Database, *, now: datetime | None = None, status: Status = Status.IN_REVIEW, **fields) -> VideoRow:
    """Insert a video row. Candidate fields go to make_candidate, the rest to update_video."""
    candidate_fields = {k: fields.pop(k) for k in list(fields) if k in Candidate.__dataclass_fields__}
    video_id = db.insert_video(make_candidate(**candidate_fields), status, now or DEFAULT_NOW)
    if fields:
        db.update_video(video_id, **fields)
    return db.get_video(video_id)
