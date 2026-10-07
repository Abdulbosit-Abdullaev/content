"""Test data builders shared by many test files."""
from __future__ import annotations

from contentbot.models import Candidate


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
