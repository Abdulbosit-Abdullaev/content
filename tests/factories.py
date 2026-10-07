"""Test data builders shared by many test files."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from contentbot.config import Secrets, Settings, load_settings
from contentbot.models import Candidate

ROOT = Path(__file__).resolve().parent.parent


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
